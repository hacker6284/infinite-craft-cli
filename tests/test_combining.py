"""Tests for combining functions: _cached_pair, do_combine, do_history."""

import asyncio
import sys
import pytest
from unittest.mock import patch, AsyncMock, MagicMock, call

from tests.conftest import MockElement, make_mock_storage, make_mock_client

# Run with pytest when invoked directly by Bazel
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


def run_async(coro):
    """Helper to run async functions in sync tests."""
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def clear_caches(request):
    """Clear module-level caches between tests."""
    import infinite_craft_cli.cli as cli

    def _clear():
        try:
            cli._reset_test_state()
        except Exception:
            pass
        cli._pair_cache.clear()
        cli._history.clear()

    _clear()
    request.addfinalizer(_clear)
    yield
    _clear()


class TestCachedPair:
    def test_calls_client_pair(self):
        from infinite_craft_cli.cli import _cached_pair
        client = make_mock_client()
        storage = make_mock_storage()
        result_elem = MockElement("Steam", "💨")
        client.pair.return_value = result_elem
        a = MockElement("Water", "💧")
        b = MockElement("Fire", "🔥")
        with patch("infinite_craft_cli.cli._record_recipe"):
            result = run_async(_cached_pair(client, storage, a, b))
        assert result.name == "Steam"
        client.pair.assert_called_once()

    def test_corrupt_recipes_surfaces_clear_error(self, tmp_path):
        from infinite_craft_cli.cli import RecipeStoreError, _cached_pair
        client = make_mock_client()
        storage = make_mock_storage()
        client.pair.return_value = MockElement("Steam", "💨")
        corrupt = tmp_path / "recipes.json"
        corrupt.write_text('{"Steam": [["Fire", "Water"]')  # truncated (repair heuristic cannot salvage)
        with patch("infinite_craft_cli.cli.RECIPES_PATH", str(corrupt)):
            with pytest.raises(RecipeStoreError, match="recipes.json is corrupted"):
                run_async(_cached_pair(
                    client, storage, MockElement("Water", "💧"), MockElement("Fire", "🔥")
                ))

    def test_caches_result(self):
        from infinite_craft_cli.cli import _cached_pair
        client = make_mock_client()
        storage = make_mock_storage()
        result_elem = MockElement("Steam", "💨")
        client.pair.return_value = result_elem
        a = MockElement("Water", "💧")
        b = MockElement("Fire", "🔥")
        with patch("infinite_craft_cli.cli._record_recipe"):
            run_async(_cached_pair(client, storage, a, b))
            run_async(_cached_pair(client, storage, a, b))
        # Only called once due to cache
        client.pair.assert_called_once()

    def test_cache_key_is_sorted(self):
        from infinite_craft_cli.cli import _cached_pair
        client = make_mock_client()
        storage = make_mock_storage()
        result_elem = MockElement("Steam", "💨")
        client.pair.return_value = result_elem
        a = MockElement("Water", "💧")
        b = MockElement("Fire", "🔥")
        with patch("infinite_craft_cli.cli._record_recipe"):
            run_async(_cached_pair(client, storage, a, b))
            # Reversed order should hit cache
            run_async(_cached_pair(client, storage, b, a))
        client.pair.assert_called_once()

    def test_records_recipe_on_success(self):
        from infinite_craft_cli.cli import _cached_pair
        client = make_mock_client()
        storage = make_mock_storage()
        result_elem = MockElement("Steam", "💨")
        client.pair.return_value = result_elem
        a = MockElement("Water", "💧")
        b = MockElement("Fire", "🔥")
        with patch("infinite_craft_cli.cli._record_recipe") as mock_record:
            run_async(_cached_pair(client, storage, a, b))
        mock_record.assert_called_once_with("Steam", "Water", "Fire")

    def test_retries_on_failure(self):
        from infinite_craft_cli.cli import _cached_pair
        client = make_mock_client()
        storage = make_mock_storage()
        result_elem = MockElement("Steam", "💨")
        client.pair.side_effect = [Exception("fail"), Exception("fail"), result_elem]
        a = MockElement("Water", "💧")
        b = MockElement("Fire", "🔥")
        with (
            patch("infinite_craft_cli.cli._record_recipe"),
            patch("infinite_craft_cli.cli._sleep_cancellable_async", new_callable=AsyncMock, return_value=False),
        ):
            result = run_async(_cached_pair(client, storage, a, b))
        assert result.name == "Steam"
        assert client.pair.call_count == 3

    def test_cached_pair_cancelled_during_retry_backoff(self):
        import infinite_craft_cli.cli as cli
        from infinite_craft_cli.cli import CommandCancelled, _cached_pair

        cli._cancelled = False
        client = make_mock_client()
        storage = make_mock_storage()
        client.pair.side_effect = Exception("fail")

        async def cancel_on_sleep(seconds, step=0.1):
            cli._cancelled = True
            return True

        a = MockElement("Water", "💧")
        b = MockElement("Fire", "🔥")
        with (
            patch("infinite_craft_cli.cli._sleep_cancellable_async", side_effect=cancel_on_sleep),
            pytest.raises(CommandCancelled),
        ):
            run_async(_cached_pair(client, storage, a, b))
        assert client.pair.call_count == 1
        cli._cancelled = False

    def test_cached_pair_maps_rate_limit_cancelled_to_command_cancelled(self):
        import infinite_craft_cli.cli as cli
        from infinite_craft_cli.ratelimit import RateLimitCancelled
        from infinite_craft_cli.cli import CommandCancelled, _cached_pair

        client = make_mock_client()
        storage = make_mock_storage()
        client.pair.side_effect = RateLimitCancelled()
        a = MockElement("Water", "💧")
        b = MockElement("Fire", "🔥")
        with (
            patch("infinite_craft_cli.cli._record_recipe") as mock_record,
            pytest.raises(CommandCancelled),
        ):
            run_async(_cached_pair(client, storage, a, b))
        assert client.pair.call_count == 1
        mock_record.assert_not_called()
        assert ("Water", "Fire") not in cli._pair_cache and ("Fire", "Water") not in cli._pair_cache

    def test_cached_pair_raises_command_cancelled_when_already_cancelled(self):
        import infinite_craft_cli.cli as cli
        from infinite_craft_cli.cli import CommandCancelled, _cached_pair

        cli._cancelled = True
        client = make_mock_client()
        storage = make_mock_storage()
        a = MockElement("Water", "💧")
        b = MockElement("Fire", "🔥")
        with pytest.raises(CommandCancelled):
            run_async(_cached_pair(client, storage, a, b))
        client.pair.assert_not_called()
        cli._cancelled = False

    def test_raises_after_max_retries(self):
        from infinite_craft_cli.cli import _cached_pair
        client = make_mock_client()
        storage = make_mock_storage()
        client.pair.side_effect = Exception("persistent failure")
        a = MockElement("Water", "💧")
        b = MockElement("Fire", "🔥")
        with (
            patch("infinite_craft_cli.cli._sleep_cancellable_async", new_callable=AsyncMock, return_value=False),
            pytest.raises(Exception, match="persistent failure"),
        ):
            run_async(_cached_pair(client, storage, a, b))
        assert client.pair.call_count == 3

    def test_no_recipe_on_none_result(self):
        from infinite_craft_cli.cli import _cached_pair
        client = make_mock_client()
        storage = make_mock_storage()
        result_elem = MockElement("", "")
        result_elem.name = None
        client.pair.return_value = result_elem
        a = MockElement("Water", "💧")
        b = MockElement("Water", "💧")
        with patch("infinite_craft_cli.cli._record_recipe") as mock_record:
            run_async(_cached_pair(client, storage, a, b))
        mock_record.assert_not_called()


class TestDoCombine:
    def test_successful_combine(self):
        from infinite_craft_cli.cli import do_combine
        client = make_mock_client()
        storage = make_mock_storage()
        result_elem = MockElement("Steam", "💨")
        client.pair.return_value = result_elem
        with patch("infinite_craft_cli.cli._record_recipe"):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = False
                result = run_async(do_combine(client, storage, "Water", "Fire"))
        assert "Steam" in result
        assert "=" in result
        storage.add.assert_any_call(name='Steam', emoji='💨', is_first_discovery=False)

    def test_api_error(self):
        from infinite_craft_cli.cli import do_combine
        client = make_mock_client()
        storage = make_mock_storage()
        client.pair.side_effect = Exception("API down")
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False
            result = run_async(do_combine(client, storage, "Water", "Fire"))
        assert "Error" in result

    def test_updates_history(self):
        from infinite_craft_cli.cli import do_combine, _history
        client = make_mock_client()
        storage = make_mock_storage()
        result_elem = MockElement("Steam", "💨")
        client.pair.return_value = result_elem
        with patch("infinite_craft_cli.cli._record_recipe"):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = False
                run_async(do_combine(client, storage, "Water", "Fire"))
        assert len(_history) == 1
        assert _history[0] == ("Water", "Fire", "Steam")

    def test_updates_discoveries(self):
        from infinite_craft_cli.cli import do_combine
        client = make_mock_client()
        storage = make_mock_storage()
        result_elem = MockElement("Steam", "💨")
        client.pair.return_value = result_elem
        with patch("infinite_craft_cli.cli._record_recipe"):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = False
                run_async(do_combine(client, storage, "Water", "Fire"))
        # Both inputs and the result should be added to storage
        calls = storage.add.call_args_list
        assert len(calls) == 3
        # Inputs
        assert calls[0] == call(name='Water', emoji='💧', is_first_discovery=False)
        assert calls[1] == call(name='Fire', emoji='🔥', is_first_discovery=False)
        # Result
        assert calls[2] == call(name='Steam', emoji='💨', is_first_discovery=False)

    def test_first_discovery_flag_preserved(self):
        from infinite_craft_cli.cli import do_combine
        client = make_mock_client()
        storage = make_mock_storage()
        result_elem = MockElement("Unicorn", "🦄", is_first_discovery=True)
        client.pair.return_value = result_elem
        with patch("infinite_craft_cli.cli._record_recipe"):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = False
                run_async(do_combine(client, storage, "Water", "Fire"))
        storage.add.assert_any_call(name='Unicorn', emoji='🦄', is_first_discovery=True)

    def test_nothing_result_no_discovery_update(self):
        from infinite_craft_cli.cli import do_combine
        client = make_mock_client()
        storage = make_mock_storage()
        result_elem = MagicMock()
        result_elem.name = None
        client.pair.return_value = result_elem
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False
            run_async(do_combine(client, storage, "Water", "Water"))
        storage.add.assert_not_called()

    def test_do_combine_propagates_command_cancelled(self):
        import infinite_craft_cli.cli as cli
        from infinite_craft_cli.cli import CommandCancelled, do_combine

        cli._cancelled = True
        client = make_mock_client()
        storage = make_mock_storage()
        with pytest.raises(CommandCancelled):
            run_async(do_combine(client, storage, "Water", "Fire"))
        client.pair.assert_not_called()
        cli._cancelled = False


class TestCombinePairsCancel:
    def test_combine_pairs_partial_batch_first_cancel_second_success(self, capsys):
        import infinite_craft_cli.cli as cli
        from infinite_craft_cli.cli import CommandCancelled, _combine_pairs

        cli._cancelled = False
        client = make_mock_client()
        storage = make_mock_storage()
        pairs = [
            (MockElement("A", "🔹"), MockElement("B", "🔸")),
            (MockElement("C", "🔹"), MockElement("D", "🔸")),
        ]
        call_num = 0

        async def cached_side_effect(_client, _storage, a, b):
            nonlocal call_num
            call_num += 1
            if call_num == 1:
                cli._cancelled = True
                raise CommandCancelled()
            return MockElement("Steam", "💨")

        with (
            patch("infinite_craft_cli.cli._cached_pair", side_effect=cached_side_effect),
            patch("infinite_craft_cli.cli._record_recipe"),
        ):
            run_async(_combine_pairs(client, storage, pairs))

        out = capsys.readouterr().out
        assert "Cancelled." in out
        assert "Error:" not in out
        assert call_num == 2
        assert "C + " in out and "D = " in out
        assert "Steam" in out
        cli._reset_cancelled()

    def test_combine_pairs_command_cancelled_no_error_lines(self, capsys):
        import infinite_craft_cli.cli as cli
        from infinite_craft_cli.cli import CommandCancelled, _combine_pairs

        cli._cancelled = True
        client = make_mock_client()
        storage = make_mock_storage()
        pairs = [
            (MockElement("A", "🔹"), MockElement("B", "🔸")),
            (MockElement("C", "🔹"), MockElement("D", "🔸")),
        ]

        async def raise_cancel(*_args, **_kwargs):
            raise CommandCancelled()

        with patch("infinite_craft_cli.cli._cached_pair", side_effect=raise_cancel):
            run_async(_combine_pairs(client, storage, pairs))

        out = capsys.readouterr().out
        assert "Cancelled." in out
        assert "Error:" not in out
        cli._reset_cancelled()


class TestDoHistory:
    def test_empty_history(self):
        from infinite_craft_cli.cli import do_history
        result = do_history()
        assert "No combinations tried" in result

    def test_with_entries(self):
        from infinite_craft_cli.cli import do_history, _history
        _history.append(("Water", "Fire", "Steam"))
        _history.append(("Earth", "Water", "Mud"))
        result = do_history()
        assert "1. Water + Fire = Steam" in result
        assert "2. Earth + Water = Mud" in result

    def test_numbering(self):
        from infinite_craft_cli.cli import do_history, _history
        for i in range(5):
            _history.append((f"A{i}", f"B{i}", f"C{i}"))
        result = do_history()
        assert "5. A4 + B4 = C4" in result
