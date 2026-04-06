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
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def clear_caches():
    """Clear module-level caches between tests."""
    import infinite_craft_cli.cli as cli
    cli._pair_cache.clear()
    cli._history.clear()
    yield
    cli._pair_cache.clear()
    cli._history.clear()


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
        with patch("infinite_craft_cli.cli._record_recipe"):
            result = run_async(_cached_pair(client, storage, a, b))
        assert result.name == "Steam"
        assert client.pair.call_count == 3

    def test_raises_after_max_retries(self):
        from infinite_craft_cli.cli import _cached_pair
        client = make_mock_client()
        storage = make_mock_storage()
        client.pair.side_effect = Exception("persistent failure")
        a = MockElement("Water", "💧")
        b = MockElement("Fire", "🔥")
        with pytest.raises(Exception, match="persistent failure"):
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
