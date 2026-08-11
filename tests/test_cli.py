"""Tests for CLI entry point: main() argparse, noninteractive_mode."""

import asyncio
import sys
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from tests.conftest import MockElement, make_mock_storage, make_mock_client

# Run with pytest when invoked directly by Bazel
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


def run_async(coro):
    # modernized (was deprecated get_event_loop)
    return asyncio.run(asyncio.wait_for(coro, timeout=30.0))


class TestNonInteractiveMode:
    def test_combine_command(self, capsys):
        from infinite_craft_cli.cli import noninteractive_mode

        args = MagicMock()
        args.command = "combine"
        args.first = "Water"
        args.second = "Fire"
        client = make_mock_client()
        storage = make_mock_storage()
        result_elem = MockElement("Steam", "💨")
        client.pair.return_value = result_elem

        mock_client_cls = MagicMock()
        mock_client_ctx = AsyncMock()
        mock_client_ctx.__aenter__.return_value = client
        mock_client_ctx.__aexit__.return_value = False
        mock_client_cls.return_value = mock_client_ctx

        mock_storage_cls = MagicMock()
        mock_storage_cls.return_value = storage

        with patch("infinite_craft_cli.cli.InfiniteCraftClient", mock_client_cls):
            with patch("infinite_craft_cli.cli.DiscoveryStorage", mock_storage_cls):
                with patch("infinite_craft_cli.cli._record_recipes_batch"):
                    with patch("sys.stdout.isatty", return_value=False):
                        run_async(noninteractive_mode(args))
        captured = capsys.readouterr()
        assert "Steam" in captured.out

    def test_search_command(self, capsys):
        from infinite_craft_cli.cli import noninteractive_mode

        args = MagicMock()
        args.command = "search"
        args.query = "water"
        storage = make_mock_storage()

        mock_storage_cls = MagicMock()
        mock_storage_cls.return_value = storage

        with patch("infinite_craft_cli.cli.DiscoveryStorage", mock_storage_cls):
            with patch("sys.stdout.isatty", return_value=False):
                run_async(noninteractive_mode(args))
        captured = capsys.readouterr()
        assert "Water" in captured.out

    def test_list_command(self, capsys):
        from infinite_craft_cli.cli import noninteractive_mode

        args = MagicMock()
        args.command = "list"
        storage = make_mock_storage()

        mock_storage_cls = MagicMock()
        mock_storage_cls.return_value = storage

        with patch("infinite_craft_cli.cli.DiscoveryStorage", mock_storage_cls):
            with patch("sys.stdout.isatty", return_value=False):
                run_async(noninteractive_mode(args))
        captured = capsys.readouterr()
        assert "4 elements" in captured.out

    def test_with_command(self, capsys):
        from infinite_craft_cli.cli import noninteractive_mode

        args = MagicMock()
        args.command = "with"
        args.element = "Water"
        args.query = "/^fi/"
        client = make_mock_client()
        storage = make_mock_storage(
            [
                MockElement("Water", "💧"),
                MockElement("Fire", "🔥"),
                MockElement("Firewall", "🧱"),
            ]
        )
        nothing = MagicMock()
        nothing.name = None
        client.pair.return_value = nothing

        mock_client_cls = MagicMock()
        mock_client_ctx = AsyncMock()
        mock_client_ctx.__aenter__.return_value = client
        mock_client_ctx.__aexit__.return_value = False
        mock_client_cls.return_value = mock_client_ctx

        mock_storage_cls = MagicMock()
        mock_storage_cls.return_value = storage

        with patch("infinite_craft_cli.cli.InfiniteCraftClient", mock_client_cls):
            with patch("infinite_craft_cli.cli.DiscoveryStorage", mock_storage_cls):
                with patch("sys.stdout.isatty", return_value=False):
                    run_async(noninteractive_mode(args))
        captured = capsys.readouterr()
        assert "Combining" in captured.out

    def test_cross_command(self, capsys):
        from infinite_craft_cli.cli import noninteractive_mode

        args = MagicMock()
        args.command = "cross"
        args.left = "/^fi/"
        args.right = "/^wa/"
        client = make_mock_client()
        storage = make_mock_storage(
            [
                MockElement("Water", "💧"),
                MockElement("Fire", "🔥"),
                MockElement("Firewall", "🧱"),
            ]
        )
        nothing = MagicMock()
        nothing.name = None
        client.pair.return_value = nothing

        mock_client_cls = MagicMock()
        mock_client_ctx = AsyncMock()
        mock_client_ctx.__aenter__.return_value = client
        mock_client_ctx.__aexit__.return_value = False
        mock_client_cls.return_value = mock_client_ctx

        mock_storage_cls = MagicMock()
        mock_storage_cls.return_value = storage

        with patch("infinite_craft_cli.cli.InfiniteCraftClient", mock_client_cls):
            with patch("infinite_craft_cli.cli.DiscoveryStorage", mock_storage_cls):
                with patch("sys.stdout.isatty", return_value=False):
                    run_async(noninteractive_mode(args))
        captured = capsys.readouterr()
        assert "unique pairs" in captured.out

    def test_exhaust_command(self, capsys):
        from infinite_craft_cli.cli import noninteractive_mode

        args = MagicMock()
        args.command = "exhaust"
        args.query = "water"
        client = make_mock_client()
        storage = make_mock_storage()
        nothing = MagicMock()
        nothing.name = None
        client.pair.return_value = nothing

        mock_client_cls = MagicMock()
        mock_client_ctx = AsyncMock()
        mock_client_ctx.__aenter__.return_value = client
        mock_client_ctx.__aexit__.return_value = False
        mock_client_cls.return_value = mock_client_ctx

        mock_storage_cls = MagicMock()
        mock_storage_cls.return_value = storage

        with patch("infinite_craft_cli.cli.InfiniteCraftClient", mock_client_cls):
            with patch("infinite_craft_cli.cli.DiscoveryStorage", mock_storage_cls):
                with patch("infinite_craft_cli.cli._record_recipes_batch"):
                    with patch("sys.stdout.isatty", return_value=False):
                        run_async(noninteractive_mode(args))
        captured = capsys.readouterr()
        assert "Exhausting" in captured.out

    def test_permutate_command(self, capsys):
        from infinite_craft_cli.cli import noninteractive_mode

        args = MagicMock()
        args.command = "permutate"
        args.query = "w*"
        client = make_mock_client()
        storage = make_mock_storage(
            [
                MockElement("Water", "💧"),
                MockElement("Wind", "🌬️"),
            ]
        )
        nothing = MagicMock()
        nothing.name = None
        client.pair.return_value = nothing

        mock_client_cls = MagicMock()
        mock_client_ctx = AsyncMock()
        mock_client_ctx.__aenter__.return_value = client
        mock_client_ctx.__aexit__.return_value = False
        mock_client_cls.return_value = mock_client_ctx

        mock_storage_cls = MagicMock()
        mock_storage_cls.return_value = storage

        with patch("infinite_craft_cli.cli.InfiniteCraftClient", mock_client_cls):
            with patch("infinite_craft_cli.cli.DiscoveryStorage", mock_storage_cls):
                with patch("infinite_craft_cli.cli._record_recipes_batch"):
                    with patch("sys.stdout.isatty", return_value=False):
                        run_async(noninteractive_mode(args))
        captured = capsys.readouterr()
        assert "Permuting matches for" in captured.out or "Permutating" in captured.out


class TestArgParsing:
    def test_version_flag(self):
        from infinite_craft_cli.cli import main

        with patch("sys.argv", ["infinite-craft", "--version"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0
