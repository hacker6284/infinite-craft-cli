"""Tests for formatting functions: _color, format_element, format_result, do_help."""

import sys
import pytest
from unittest.mock import patch, MagicMock

from tests.conftest import MockElement

# Run with pytest when invoked directly by Bazel
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


class TestColor:
    def test_returns_ansi_when_tty(self):
        from infinite_craft_cli.cli import _color, BOLD, RESET
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = True
            result = _color("hello", BOLD)
            assert BOLD in result
            assert RESET in result
            assert "hello" in result

    def test_returns_plain_when_not_tty(self):
        from infinite_craft_cli.cli import _color, BOLD, RESET
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False
            result = _color("hello", BOLD)
            assert result == "hello"
            assert BOLD not in result

    def test_empty_string(self):
        from infinite_craft_cli.cli import _color, GREEN
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False
            assert _color("", GREEN) == ""

    def test_special_characters(self):
        from infinite_craft_cli.cli import _color, CYAN
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False
            assert _color("🔥 fire!", CYAN) == "🔥 fire!"


class TestFormatElement:
    def test_regular_element(self):
        from infinite_craft_cli.cli import format_element
        elem = MockElement("Steam", "💨")
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False
            result = format_element(elem)
            assert result == "💨 Steam"

    def test_first_discovery_element(self):
        from infinite_craft_cli.cli import format_element
        elem = MockElement("Unicorn", "🦄", is_first_discovery=True)
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False
            result = format_element(elem)
            assert "🦄 Unicorn" in result
            assert "[FIRST DISCOVERY!]" in result

    def test_element_without_emoji(self):
        from infinite_craft_cli.cli import format_element
        elem = MockElement("Mud", "")
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False
            result = format_element(elem)
            assert result == "Mud"


class TestFormatResult:
    def test_successful_result(self):
        from infinite_craft_cli.cli import format_result
        result = MockElement("Steam", "💨")
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False
            output = format_result("💧 Water", "🔥 Fire", result)
            assert "💧 Water" in output
            assert "🔥 Fire" in output
            assert "=" in output
            assert "💨 Steam" in output

    def test_nothing_result(self):
        from infinite_craft_cli.cli import format_result
        result = MagicMock()
        result.name = None
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False
            output = format_result("Water", "Water", result)
            assert "Nothing" in output


class TestDoHelp:
    def test_returns_string(self):
        from infinite_craft_cli.cli import do_help
        result = do_help()
        assert isinstance(result, str)

    def test_contains_commands(self):
        from infinite_craft_cli.cli import do_help
        result = do_help()
        assert "/search" in result
        assert "/recipe" in result
        assert "/list" in result
        assert "/help" in result
        assert "/quit" in result

    def test_contains_operators(self):
        from infinite_craft_cli.cli import do_help
        result = do_help()
        assert "++" in result
        assert "+ |" in result
