"""Tests for combining functions: _cached_pair, do_combine, do_history."""

import asyncio
import sys
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from tests.conftest import MockElement, make_mock_game

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
    def test_calls_game_pair(self):
        from infinite_craft_cli.cli import _cached_pair
        game = make_mock_game()
        result_elem = MockElement("Steam", "💨")
        game.pair.return_value = result_elem
        a = MockElement("Water", "💧")
        b = MockElement("Fire", "🔥")
        with patch("infinite_craft_cli.cli._record_recipe"):
            result = run_async(_cached_pair(game, a, b))
        assert result.name == "Steam"
        game.pair.assert_called_once()

    def test_caches_result(self):
        from infinite_craft_cli.cli import _cached_pair
        game = make_mock_game()
        result_elem = MockElement("Steam", "💨")
        game.pair.return_value = result_elem
        a = MockElement("Water", "💧")
        b = MockElement("Fire", "🔥")
        with patch("infinite_craft_cli.cli._record_recipe"):
            run_async(_cached_pair(game, a, b))
            run_async(_cached_pair(game, a, b))
        # Only called once due to cache
        game.pair.assert_called_once()

    def test_cache_key_is_sorted(self):
        from infinite_craft_cli.cli import _cached_pair
        game = make_mock_game()
        result_elem = MockElement("Steam", "💨")
        game.pair.return_value = result_elem
        a = MockElement("Water", "💧")
        b = MockElement("Fire", "🔥")
        with patch("infinite_craft_cli.cli._record_recipe"):
            run_async(_cached_pair(game, a, b))
            # Reversed order should hit cache
            run_async(_cached_pair(game, b, a))
        game.pair.assert_called_once()

    def test_records_recipe_on_success(self):
        from infinite_craft_cli.cli import _cached_pair
        game = make_mock_game()
        result_elem = MockElement("Steam", "💨")
        game.pair.return_value = result_elem
        a = MockElement("Water", "💧")
        b = MockElement("Fire", "🔥")
        with patch("infinite_craft_cli.cli._record_recipe") as mock_record:
            run_async(_cached_pair(game, a, b))
        mock_record.assert_called_once_with("Steam", "Water", "Fire")

    def test_no_recipe_on_none_result(self):
        from infinite_craft_cli.cli import _cached_pair
        game = make_mock_game()
        result_elem = MockElement("", "")
        result_elem.name = None
        game.pair.return_value = result_elem
        a = MockElement("Water", "💧")
        b = MockElement("Water", "💧")
        with patch("infinite_craft_cli.cli._record_recipe") as mock_record:
            run_async(_cached_pair(game, a, b))
        mock_record.assert_not_called()


class TestDoCombine:
    def test_successful_combine(self):
        from infinite_craft_cli.cli import do_combine
        game = make_mock_game()
        result_elem = MockElement("Steam", "💨")
        game.pair.return_value = result_elem
        with patch("infinite_craft_cli.cli._record_recipe"):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = False
                result = run_async(do_combine(game, "Water", "Fire"))
        assert "Steam" in result
        assert "=" in result

    def test_api_error(self):
        from infinite_craft_cli.cli import do_combine
        game = make_mock_game()
        game.pair.side_effect = Exception("API down")
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False
            result = run_async(do_combine(game, "Water", "Fire"))
        assert "Error" in result

    def test_updates_history(self):
        from infinite_craft_cli.cli import do_combine, _history
        game = make_mock_game()
        result_elem = MockElement("Steam", "💨")
        game.pair.return_value = result_elem
        with patch("infinite_craft_cli.cli._record_recipe"):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = False
                run_async(do_combine(game, "Water", "Fire"))
        assert len(_history) == 1
        assert _history[0] == ("Water", "Fire", "Steam")

    def test_updates_discoveries(self):
        from infinite_craft_cli.cli import do_combine
        game = make_mock_game()
        result_elem = MockElement("Steam", "💨")
        game.pair.return_value = result_elem
        with patch("infinite_craft_cli.cli._record_recipe"):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = False
                run_async(do_combine(game, "Water", "Fire"))
        # Both inputs should be updated in discoveries
        assert game._update_discoveries.call_count == 2

    def test_nothing_result_no_discovery_update(self):
        from infinite_craft_cli.cli import do_combine
        game = make_mock_game()
        result_elem = MagicMock()
        result_elem.name = None
        game.pair.return_value = result_elem
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False
            run_async(do_combine(game, "Water", "Water"))
        game._update_discoveries.assert_not_called()


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
