"""Tests for formatting functions: _color, format_element, format_result, do_help."""

import sys
from pathlib import Path

import pytest
from unittest.mock import patch, MagicMock

from tests.conftest import MockElement
from tests.help_utils import (
    assert_help_dual_formats,
    assert_help_query_syntax_once,
    assert_help_text_clean,
    extract_js_help_plaintext,
    read_index_html_commands,
    read_readme_commands_section,
)

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
        from infinite_craft_cli.cli import _color, BOLD

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
        from infinite_craft_cli.cli import _color, CYAN, _ansi_visible_len, _fit_visible

        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False
            assert _color("🔥 fire!", CYAN) == "🔥 fire!"
        # emoji + CJK width cases (1-col ascii, 2-col emoji/CJK)
        assert _ansi_visible_len("a") == 1
        assert _ansi_visible_len("🐦 Phoenix") == 10  # 2 (🐦) + 1 (space) + 7
        assert _ansi_visible_len("中") == 2
        assert _ansi_visible_len("🔥[FIRST]") == 9
        assert _fit_visible("🐦🔥longname", 3) == "🐦"
        assert _fit_visible("🐦🔥long", 4) == "🐦🔥"
        assert _fit_visible("abc🐦def", 4) == "abc"
        assert _fit_visible("a🐦b", 3) == "a🐦"
        assert _fit_visible("🐦a", 2) == "🐦"
        # complex grapheme/ZWJ/variant + FIRST (full support)
        assert _ansi_visible_len("🌬️") == 2
        assert _ansi_visible_len("👨\u200d👩\u200d👧") == 2
        assert _ansi_visible_len("👨\u200d👩\u200d👧[FIRST]") == 9
        assert _fit_visible("👨\u200d👩\u200d👧🔥longname", 3) == "👨\u200d👩\u200d👧"
        assert _fit_visible("🌬️ab", 3) == "🌬️a"
        assert _fit_visible("👨\u200d👩\u200d👧", 2) == "👨\u200d👩\u200d👧"
        assert _fit_visible("👨\u200d👩\u200d👧", 1) == ""


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

    def test_tty_strips_control_chars(self):
        from infinite_craft_cli.cli import format_element

        elem = MockElement("Evil\x1b[31mName\x07", "💀")
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = True
            result = format_element(elem)
        assert "\x1b" not in result
        assert "\x07" not in result
        assert "Evil" in result
        assert "Name" in result


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

    def test_tty_strips_control_chars_in_operands(self):
        from infinite_craft_cli.cli import format_result

        result = MockElement("Steam", "💨")
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = True
            output = format_result("Evil\x1b[31mA\x07", "Bad\x07B", result)
        assert "\x1b" not in output
        assert "\x07" not in output
        assert "Evil" in output
        assert "BadB" in output
        assert "Steam" in output


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
        assert "/clear" in result
        assert "/help" in result
        assert "/quit" in result
        assert "/combine" in result
        assert "/with" in result
        assert "/cross" in result
        assert "/exhaust" in result
        assert "/permutate" in result

    def test_contains_operators(self):
        from infinite_craft_cli.cli import do_help

        result = do_help()
        assert "++" in result
        assert "+|" in result
        assert " * " in result

    def test_help_text_clean_and_structured(self):
        from infinite_craft_cli.cli import do_help

        result = do_help()
        assert "/pattern/" in result
        assert "!<query>" in result
        assert "Exclude matches" in result
        assert "All elements (exclude nothing)" in result
        assert "^<query>" in result
        assert "First discoveries only" in result
        assert "fnmatch" in result.lower() or "* ? []" in result
        assert_help_text_clean(result)
        assert_help_dual_formats(result)
        assert_help_query_syntax_once(result)

    def test_js_help_matches_python_structure(self):
        js_help = extract_js_help_plaintext()
        assert_help_text_clean(js_help)
        assert_help_dual_formats(js_help)
        assert_help_query_syntax_once(js_help)

    def test_readme_commands_section_clean(self):
        readme = read_readme_commands_section()
        assert_help_text_clean(readme)
        assert "/combine <element> <element>" in readme
        assert "+|" in readme
        assert "then `+|`" in readme
        assert "/queue" in readme

    def test_index_html_commands_clean(self):
        html = read_index_html_commands()
        assert_help_text_clean(html)
        assert "/combine &lt;element&gt; &lt;element&gt;" in html
        assert "/permutate" in html
        assert "/exhaust" in html
        assert "Clear output" in html


class TestTrainerMinJs:
    def test_min_js_contains_key_help_strings(self):
        min_js = (
            Path(__file__).resolve().parent.parent / "bookmarklet" / "trainer.min.js"
        ).read_text()
        for needle in (
            "no space between + and |",
            "/combine <element> <element>",
            "Use <element> +| <query>",
            "Unknown command:",
            "Already queued.",
        ):
            assert needle in min_js, f"missing {needle!r} in trainer.min.js"
