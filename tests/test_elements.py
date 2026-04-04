"""Tests for element resolution and matching: _resolve_element, _match_elements, do_search, do_list."""

import sys
import pytest
from unittest.mock import patch

from tests.conftest import MockElement, make_mock_game

# Run with pytest when invoked directly by Bazel
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


class TestResolveElement:
    def test_found_in_discoveries(self, mock_game):
        from infinite_craft_cli.cli import _resolve_element
        result = _resolve_element(mock_game, "Water")
        assert result.name == "Water"
        assert result.emoji == "💧"

    def test_title_case_fallback(self):
        from infinite_craft_cli.cli import _resolve_element
        game = make_mock_game([MockElement("Steam", "💨")])
        result = _resolve_element(game, "steam")
        assert result.name == "Steam"

    def test_not_found_returns_new_element(self, mock_game):
        from infinite_craft_cli.cli import _resolve_element
        result = _resolve_element(mock_game, "unicorn")
        assert result.name == "Unicorn"

    def test_whitespace_stripped(self, mock_game):
        from infinite_craft_cli.cli import _resolve_element
        result = _resolve_element(mock_game, "  Water  ")
        # Should find Water since title-case of stripped " Water " is "Water"
        # or direct lookup finds it
        assert result.name == "Water"

    def test_exact_match_preferred_over_title(self):
        from infinite_craft_cli.cli import _resolve_element
        game = make_mock_game([
            MockElement("pH", ""),
            MockElement("Ph", ""),
        ])
        result = _resolve_element(game, "pH")
        assert result.name == "pH"


class TestMatchElements:
    def test_substring_match(self, mock_game_with_extras):
        from infinite_craft_cli.cli import _match_elements
        matches = _match_elements(mock_game_with_extras, "water")
        names = [e.name for e in matches]
        assert "Water" in names
        assert "Waterfall" in names

    def test_wildcard_match(self, mock_game_with_extras):
        from infinite_craft_cli.cli import _match_elements
        matches = _match_elements(mock_game_with_extras, "fire*")
        names = [e.name for e in matches]
        assert "Fire" in names
        assert "Firewall" in names
        assert "Water" not in names

    def test_question_mark_wildcard(self, mock_game_with_extras):
        from infinite_craft_cli.cli import _match_elements
        matches = _match_elements(mock_game_with_extras, "mu?")
        names = [e.name for e in matches]
        assert "Mud" in names

    def test_first_discovery_filter(self, mock_game_with_extras):
        from infinite_craft_cli.cli import _match_elements
        matches = _match_elements(mock_game_with_extras, "^*")
        assert all(e.is_first_discovery for e in matches)
        names = [e.name for e in matches]
        assert "Waterfall" in names
        assert "Firewall" in names
        assert "Water" not in names

    def test_no_matches(self, mock_game_with_extras):
        from infinite_craft_cli.cli import _match_elements
        matches = _match_elements(mock_game_with_extras, "zzzznothing")
        assert matches == []

    def test_case_insensitive(self, mock_game_with_extras):
        from infinite_craft_cli.cli import _match_elements
        matches = _match_elements(mock_game_with_extras, "WATER")
        names = [e.name for e in matches]
        assert "Water" in names

    def test_bracket_pattern(self, mock_game_with_extras):
        from infinite_craft_cli.cli import _match_elements
        matches = _match_elements(mock_game_with_extras, "[md]u*")
        names = [e.name for e in matches]
        assert "Mud" in names
        assert "Dust" in names


class TestDoSearch:
    def test_no_matches(self, mock_game):
        from infinite_craft_cli.cli import do_search
        result = do_search(mock_game, "zzz")
        assert "No matches found" in result

    def test_single_match(self, mock_game):
        from infinite_craft_cli.cli import do_search
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False
            result = do_search(mock_game, "water")
            assert "Water" in result

    def test_multiple_matches(self, mock_game_with_extras):
        from infinite_craft_cli.cli import do_search
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False
            result = do_search(mock_game_with_extras, "fire")
            assert "Fire" in result
            assert "Firewall" in result


class TestDoList:
    def test_lists_all_elements(self, mock_game):
        from infinite_craft_cli.cli import do_list
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False
            result = do_list(mock_game)
            assert "4 elements" in result
            assert "Water" in result
            assert "Fire" in result

    def test_empty_discoveries(self):
        from infinite_craft_cli.cli import do_list
        game = make_mock_game([])
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False
            result = do_list(game)
            assert "0 elements" in result
