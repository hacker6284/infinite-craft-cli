"""Tests for interactive mode command parsing and dispatch."""

import asyncio
import sys
import pytest
from unittest.mock import patch, AsyncMock, MagicMock, call

from tests.conftest import MockElement, make_mock_storage, make_mock_client

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


def run_async(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def clear_caches(tmp_path):
    import infinite_craft_cli.cli as cli
    cli._pair_cache.clear()
    cli._history.clear()
    # Use a temp discoveries file so we don't load real user data
    with patch("infinite_craft_cli.cli.DISCOVERIES_PATH", str(tmp_path / "discoveries.json")):
        yield
    cli._pair_cache.clear()
    cli._history.clear()


def _run_interactive(inputs):
    """Run interactive_mode with a sequence of input lines, return captured output."""
    from infinite_craft_cli.cli import interactive_mode
    input_iter = iter(inputs + ["/quit"])
    with patch("builtins.input", side_effect=input_iter):
        run_async(interactive_mode())


class TestInteractiveCombine:
    def test_plus_calls_do_combine(self, capsys):
        from infinite_craft_cli.cli import interactive_mode
        result_elem = MockElement("Steam", "💨")

        with patch("infinite_craft_cli.cli.InfiniteCraftClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.pair = AsyncMock(return_value=result_elem)
            with patch("infinite_craft_cli.cli._record_recipe"):
                _run_interactive(["Water + Fire"])

        captured = capsys.readouterr()
        assert "Steam" in captured.out

    def test_empty_input_ignored(self, capsys):
        with patch("infinite_craft_cli.cli.InfiniteCraftClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            _run_interactive(["", "  ", ""])

        captured = capsys.readouterr()
        assert "Unknown" not in captured.out


class TestInteractiveCommands:
    """Test that /commands dispatch to the correct functions."""

    def _make_client_context(self):
        mock_client = AsyncMock()
        patcher = patch("infinite_craft_cli.cli.InfiniteCraftClient")
        MockClient = patcher.start()
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        return mock_client, patcher

    def test_search_command(self, capsys):
        mock_client, patcher = self._make_client_context()
        try:
            _run_interactive(["/search Water"])
        finally:
            patcher.stop()
        captured = capsys.readouterr()
        assert "Water" in captured.out

    def test_list_command(self, capsys):
        mock_client, patcher = self._make_client_context()
        try:
            _run_interactive(["/list"])
        finally:
            patcher.stop()
        captured = capsys.readouterr()
        assert "Discovered" in captured.out

    def test_help_command(self, capsys):
        mock_client, patcher = self._make_client_context()
        try:
            _run_interactive(["/help"])
        finally:
            patcher.stop()
        captured = capsys.readouterr()
        assert "Commands" in captured.out

    def test_history_command(self, capsys):
        mock_client, patcher = self._make_client_context()
        try:
            _run_interactive(["/history"])
        finally:
            patcher.stop()
        captured = capsys.readouterr()
        assert "No combinations tried" in captured.out

    def test_unknown_input(self, capsys):
        mock_client, patcher = self._make_client_context()
        try:
            _run_interactive(["gibberish"])
        finally:
            patcher.stop()
        captured = capsys.readouterr()
        assert "Unknown input" in captured.out

    def test_search_no_query(self, capsys):
        mock_client, patcher = self._make_client_context()
        try:
            _run_interactive(["/search"])
        finally:
            patcher.stop()
        captured = capsys.readouterr()
        assert "Usage" in captured.out

    def test_quit_exits(self, capsys):
        mock_client, patcher = self._make_client_context()
        try:
            from infinite_craft_cli.cli import interactive_mode
            with patch("builtins.input", side_effect=["/quit"]):
                run_async(interactive_mode())
        finally:
            patcher.stop()
        captured = capsys.readouterr()
        assert "Goodbye" in captured.out

    def test_eof_exits(self, capsys):
        mock_client, patcher = self._make_client_context()
        try:
            from infinite_craft_cli.cli import interactive_mode
            with patch("builtins.input", side_effect=EOFError):
                run_async(interactive_mode())
        finally:
            patcher.stop()
        captured = capsys.readouterr()
        assert "Goodbye" in captured.out


class TestInteractiveOperators:
    """Test operator parsing: ++, +|, *"""

    def _make_client_context(self):
        mock_client = AsyncMock()
        patcher = patch("infinite_craft_cli.cli.InfiniteCraftClient")
        MockClient = patcher.start()
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        return mock_client, patcher

    def test_double_plus_calls_crawl(self, capsys):
        mock_client, patcher = self._make_client_context()
        nothing = MagicMock()
        nothing.name = None
        mock_client.pair = AsyncMock(return_value=nothing)
        try:
            with patch("infinite_craft_cli.cli._record_recipe"):
                _run_interactive(["Water ++ Fire"])
        finally:
            patcher.stop()
        captured = capsys.readouterr()
        assert "Crawling" in captured.out

    def test_plus_pipe_calls_match_and_combine(self, capsys):
        mock_client, patcher = self._make_client_context()
        nothing = MagicMock()
        nothing.name = None
        mock_client.pair = AsyncMock(return_value=nothing)
        try:
            _run_interactive(["Water +| Fire"])
        finally:
            patcher.stop()
        captured = capsys.readouterr()
        assert "Combining" in captured.out

    def test_star_calls_cross(self, capsys):
        mock_client, patcher = self._make_client_context()
        nothing = MagicMock()
        nothing.name = None
        mock_client.pair = AsyncMock(return_value=nothing)
        try:
            _run_interactive(["Water * Fire"])
        finally:
            patcher.stop()
        captured = capsys.readouterr()
        # Cross with single matches each side = 1 pair
        assert "1 unique pairs" in captured.out
