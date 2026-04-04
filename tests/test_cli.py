"""Tests for CLI entry point: main() argparse, noninteractive_mode."""

import asyncio
import sys
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from tests.conftest import MockElement, make_mock_game

# Run with pytest when invoked directly by Bazel
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


def run_async(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestArgParsing:
    def test_combine_args(self):
        from infinite_craft_cli.cli import main
        import argparse
        with patch("sys.argv", ["infinite-craft", "combine", "Water", "Fire"]):
            with patch("asyncio.run") as mock_run:
                main()
        mock_run.assert_called_once()

    def test_search_args(self):
        from infinite_craft_cli.cli import main
        with patch("sys.argv", ["infinite-craft", "search", "steam"]):
            with patch("asyncio.run") as mock_run:
                main()
        mock_run.assert_called_once()

    def test_list_args(self):
        from infinite_craft_cli.cli import main
        with patch("sys.argv", ["infinite-craft", "list"]):
            with patch("asyncio.run") as mock_run:
                main()
        mock_run.assert_called_once()

    def test_no_args_interactive(self):
        from infinite_craft_cli.cli import main
        with patch("sys.argv", ["infinite-craft"]):
            with patch("asyncio.run") as mock_run:
                main()
        mock_run.assert_called_once()


def _mock_ic_context(game):
    """Create a mock InfiniteCraft async context manager."""
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = game
    mock_ctx.__aexit__.return_value = False
    mock_ic = MagicMock()
    mock_ic.return_value = mock_ctx
    return mock_ic


class TestNonInteractiveMode:
    def test_combine_command(self, capsys):
        from infinite_craft_cli.cli import noninteractive_mode
        args = MagicMock()
        args.command = "combine"
        args.first = "Water"
        args.second = "Fire"
        game = make_mock_game()
        result_elem = MockElement("Steam", "💨")
        game.pair.return_value = result_elem

        with patch("infinite_craft_cli.cli.InfiniteCraft", _mock_ic_context(game)):
            with patch("infinite_craft_cli.cli._record_recipe"):
                with patch("sys.stdout.isatty", return_value=False):
                    run_async(noninteractive_mode(args))
        captured = capsys.readouterr()
        assert "Steam" in captured.out

    def test_search_command(self, capsys):
        from infinite_craft_cli.cli import noninteractive_mode
        args = MagicMock()
        args.command = "search"
        args.query = "water"
        game = make_mock_game()

        with patch("infinite_craft_cli.cli.InfiniteCraft", _mock_ic_context(game)):
            with patch("sys.stdout.isatty", return_value=False):
                run_async(noninteractive_mode(args))
        captured = capsys.readouterr()
        assert "Water" in captured.out

    def test_list_command(self, capsys):
        from infinite_craft_cli.cli import noninteractive_mode
        args = MagicMock()
        args.command = "list"
        game = make_mock_game()

        with patch("infinite_craft_cli.cli.InfiniteCraft", _mock_ic_context(game)):
            with patch("sys.stdout.isatty", return_value=False):
                run_async(noninteractive_mode(args))
        captured = capsys.readouterr()
        assert "4 elements" in captured.out
