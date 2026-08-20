"""Tests for formatting functions: _color, format_element."""

import sys

import pytest
from unittest.mock import patch

from tests.conftest import MockElement


# Run with pytest when invoked directly by Bazel
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


class TestColor:
    def test_special_characters(self):
        from infinite_craft_cli.cli import _ansi_visible_len, _fit_visible

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


class TestDoHelp:
    """Host help surface — thin smoke that core commands/operators appear."""

    def test_contains_commands_and_operators(self):
        from infinite_craft_cli.cli import do_help

        result = do_help()
        assert isinstance(result, str)
        for marker in (
            "/search",
            "/list",
            "/help",
            "/quit",
            "/combine",
            "/with",
            "/cross",
            "/exhaust",
            "/permutate",
            "++",
            ":=",  # v2.0: +| removed; walrus marks the script section
        ):
            assert marker in result, f"missing {marker!r} in help"
