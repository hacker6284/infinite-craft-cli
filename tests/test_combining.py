"""Tests for combining functions: _cached_pair, do_combine."""

import asyncio
import sys
import pytest
from unittest.mock import patch, AsyncMock

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

    _clear()
    request.addfinalizer(_clear)
    yield
    _clear()


class TestCachedPair:
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

    def test_caches_result_including_reversed_key(self):
        from infinite_craft_cli.cli import _cached_pair
        client = make_mock_client()
        storage = make_mock_storage()
        result_elem = MockElement("Steam", "💨")
        client.pair.return_value = result_elem
        a = MockElement("Water", "💧")
        b = MockElement("Fire", "🔥")
        with patch("infinite_craft_cli.cli._record_recipes_batch"):
            run_async(_cached_pair(client, storage, a, b))
            run_async(_cached_pair(client, storage, a, b))  # same order hits cache
            run_async(_cached_pair(client, storage, b, a))  # reversed order hits cache
        client.pair.assert_called_once()

    def test_cached_pair_cancelled_during_retry_backoff(self):
        from infinite_craft_cli.cli import CommandCancelled, _cached_pair

        client = make_mock_client()
        storage = make_mock_storage()
        client.pair.side_effect = Exception("fail")
        a = MockElement("Water", "💧")
        b = MockElement("Fire", "🔥")
        with (
            patch(
                "infinite_craft_cli.cli._sleep_cancellable_async",
                new_callable=AsyncMock,
                return_value=True,
            ),
            pytest.raises(CommandCancelled),
        ):
            run_async(_cached_pair(client, storage, a, b))
        assert client.pair.call_count == 1

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
            patch("infinite_craft_cli.cli._record_recipes_batch") as mock_record,
            pytest.raises(CommandCancelled),
        ):
            run_async(_cached_pair(client, storage, a, b))
        assert client.pair.call_count == 1
        mock_record.assert_not_called()

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
        with patch("infinite_craft_cli.cli._record_recipes_batch") as mock_record:
            run_async(_cached_pair(client, storage, a, b))
        mock_record.assert_not_called()


class TestCombinePairsCancel:
    def test_combine_pairs_command_cancelled_no_error_lines(self, capsys):
        """Cancel mid-batch must not print Error: lines."""
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
