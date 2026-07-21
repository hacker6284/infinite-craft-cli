"""Tests for element resolution and matching: _resolve_element, _match_elements, do_search, do_list."""

import sys
import pytest
from unittest.mock import patch

from tests.conftest import MockElement, make_mock_storage

# Run with pytest when invoked directly by Bazel
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


class TestResolveElement:
    def test_found_in_discoveries(self, mock_storage):
        from infinite_craft_cli.cli import _resolve_element
        result = _resolve_element(mock_storage, "Water")
        assert result.name == "Water"
        assert result.emoji == "💧"

    def test_title_case_fallback(self):
        from infinite_craft_cli.cli import _resolve_element
        storage = make_mock_storage([MockElement("Steam", "💨")])
        result = _resolve_element(storage, "steam")
        assert result.name == "Steam"

    def test_not_found_returns_new_element(self, mock_storage):
        from infinite_craft_cli.cli import _resolve_element
        result = _resolve_element(mock_storage, "unicorn")
        assert result.name == "Unicorn"

    def test_whitespace_stripped(self, mock_storage):
        from infinite_craft_cli.cli import _resolve_element
        result = _resolve_element(mock_storage, "  Water  ")
        # Should find Water since title-case of stripped " Water " is "Water"
        # or direct lookup finds it
        assert result.name == "Water"

    def test_exact_match_preferred_over_title(self):
        from infinite_craft_cli.cli import _resolve_element
        storage = make_mock_storage([
            MockElement("pH", ""),
            MockElement("Ph", ""),
        ])
        result = _resolve_element(storage, "pH")
        assert result.name == "pH"


class TestMatchElements:
    def test_substring_match(self, mock_storage_with_extras):
        from infinite_craft_cli.cli import _match_elements
        matches, _ = _match_elements(mock_storage_with_extras, "water")
        names = [e.name for e in matches]
        assert "Water" in names
        assert "Waterfall" in names

    def test_wildcard_match(self, mock_storage_with_extras):
        from infinite_craft_cli.cli import _match_elements
        matches, _ = _match_elements(mock_storage_with_extras, "fire*")
        names = [e.name for e in matches]
        assert "Fire" in names
        assert "Firewall" in names
        assert "Water" not in names

    def test_question_mark_wildcard(self, mock_storage_with_extras):
        from infinite_craft_cli.cli import _match_elements
        matches, _ = _match_elements(mock_storage_with_extras, "mu?")
        names = [e.name for e in matches]
        assert "Mud" in names

    def test_exclude_filter(self, mock_storage_with_extras):
        from infinite_craft_cli.cli import _match_elements
        matches, _ = _match_elements(mock_storage_with_extras, "!fire*")
        names = [e.name for e in matches]
        assert "Fire" not in names
        assert "Firewall" not in names
        assert "Water" in names
        assert "Waterfall" in names

    def test_exclude_bare_prefix_returns_all(self, mock_storage_with_extras):
        from infinite_craft_cli.cli import _match_elements
        for query in ("!", "!  "):
            matches, err = _match_elements(mock_storage_with_extras, query)
            assert err is None
            assert len(matches) == len(mock_storage_with_extras.get_all())

    def test_regex_match(self, mock_storage_with_extras):
        from infinite_craft_cli.cli import _match_elements
        matches, _ = _match_elements(mock_storage_with_extras, "/^fi/")
        names = [e.name for e in matches]
        assert "Fire" in names
        assert "Firewall" in names
        assert "Water" not in names

    def test_regex_exclude(self, mock_storage_with_extras):
        from infinite_craft_cli.cli import _match_elements
        matches, _ = _match_elements(mock_storage_with_extras, "!/wall/")
        names = [e.name for e in matches]
        assert "Firewall" not in names
        assert "Water" in names
        assert "Waterfall" in names

    def test_invalid_regex_returns_error(self, mock_storage_with_extras):
        from infinite_craft_cli.cli import _match_elements
        matches, err = _match_elements(mock_storage_with_extras, "/[invalid/")
        assert matches == []
        assert err == "Invalid regex pattern"

    def test_empty_regex_no_match(self, mock_storage_with_extras):
        from infinite_craft_cli.cli import _match_elements
        matches, err = _match_elements(mock_storage_with_extras, "//")
        assert matches == []
        assert err is None

    def test_first_discovery_filter(self, mock_storage_with_extras):
        from infinite_craft_cli.cli import _match_elements
        matches, _ = _match_elements(mock_storage_with_extras, "^*")
        assert all(e.is_first_discovery for e in matches)
        names = [e.name for e in matches]
        assert "Waterfall" in names
        assert "Firewall" in names
        assert "Water" not in names

    def test_first_discovery_caret_prefix(self, mock_storage_with_extras):
        from infinite_craft_cli.cli import _match_elements
        matches, _ = _match_elements(mock_storage_with_extras, "^fire*")
        assert all(e.is_first_discovery for e in matches)
        names = [e.name for e in matches]
        assert "Firewall" in names
        assert "Fire" not in names

    def test_regex_first_discovery(self, mock_storage_with_extras):
        from infinite_craft_cli.cli import _match_elements
        matches, _ = _match_elements(mock_storage_with_extras, "^/wall/")
        names = [e.name for e in matches]
        assert names == ["Firewall"]

    def test_query_too_long(self, mock_storage_with_extras):
        from infinite_craft_cli.cli import _match_elements, MAX_QUERY_LENGTH
        matches, err = _match_elements(mock_storage_with_extras, "x" * (MAX_QUERY_LENGTH + 1))
        assert matches == []
        assert "too long" in err

    def test_nested_quantifier_regex_now_matches_literally(self, mock_storage_with_extras):
        # DIVERGENCES.md ruling 7: kernel drops the "too complex"
        # nested-quantifier gate entirely, and regex.sudo has no capture
        # groups (parens are literal characters, not grouping) — "/(a+)+/"
        # now parses as literal '(' + one-or-more 'a' + one-or-more ')',
        # which simply doesn't match any fixture name here.
        from infinite_craft_cli.cli import _match_elements
        matches, err = _match_elements(mock_storage_with_extras, "/(a+)+/")
        assert matches == []
        assert err is None

    def test_alternation_quantifier_regex_now_matches_literally(self, mock_storage_with_extras):
        # DIVERGENCES.md ruling 7: "|" is now real top-level alternation
        # and there is no "too complex" gate. "/(a|aa)+/" has one
        # top-level "|", splitting into two literal branches "(a" and
        # "aa)+" (parens are literal, not grouping) — neither matches any
        # fixture name here.
        from infinite_craft_cli.cli import _match_elements
        matches, err = _match_elements(mock_storage_with_extras, "/(a|aa)+/")
        assert matches == []
        assert err is None

    def test_nested_paren_alternation_regex_now_matches_literally(self, mock_storage_with_extras):
        # DIVERGENCES.md ruling 7: same as above — "/(a|(?:aa))+b/" splits
        # on its one top-level "|" into literal branches "(a" and
        # "(?:aa))+b"; neither matches any fixture name here.
        from infinite_craft_cli.cli import _match_elements
        matches, err = _match_elements(mock_storage_with_extras, "/(a|(?:aa))+b/")
        assert matches == []
        assert err is None

    def test_no_matches(self, mock_storage_with_extras):
        from infinite_craft_cli.cli import _match_elements
        matches, _ = _match_elements(mock_storage_with_extras, "zzzznothing")
        assert matches == []

    def test_case_insensitive(self, mock_storage_with_extras):
        from infinite_craft_cli.cli import _match_elements
        matches, _ = _match_elements(mock_storage_with_extras, "WATER")
        names = [e.name for e in matches]
        assert "Water" in names

    def test_bracket_pattern(self, mock_storage_with_extras):
        from infinite_craft_cli.cli import _match_elements
        matches, _ = _match_elements(mock_storage_with_extras, "[md]u*")
        names = [e.name for e in matches]
        assert "Mud" in names
        assert "Dust" in names


class TestParseHelpers:
    def test_parse_two_elements_positional(self):
        from infinite_craft_cli.cli import _parse_two_elements
        assert _parse_two_elements("Water Fire") == ("Water", "Fire")

    def test_parse_two_elements_element_with_plus(self):
        from infinite_craft_cli.cli import _parse_two_elements
        assert _parse_two_elements("Water C++") == ("Water", "C++")

    def test_parse_two_elements_plus_in_second_arg(self):
        from infinite_craft_cli.cli import _parse_two_elements
        assert _parse_two_elements("Water + Fire") == ("Water", "+ Fire")

    def test_parse_two_elements_tight_plus_rejected(self):
        from infinite_craft_cli.cli import _parse_two_elements
        assert _parse_two_elements("Water+Fire") is None

    def test_parse_cross_queries_positional(self):
        from infinite_craft_cli.cli import _parse_cross_queries
        assert _parse_cross_queries("fire* water*") == ("fire*", "water*")

    def test_parse_cross_queries_single_arg_rejected(self):
        from infinite_craft_cli.cli import _parse_cross_queries
        assert _parse_cross_queries("fire") is None
        assert _parse_cross_queries("fire water steam") is None

    def test_parse_cross_queries_slash_substring(self):
        from infinite_craft_cli.cli import _parse_cross_queries
        assert _parse_cross_queries("fire/water steam") == ("fire/water", "steam")

    def test_parse_cross_queries_regex_tokens(self):
        from infinite_craft_cli.cli import _parse_cross_queries
        assert _parse_cross_queries("/a b/ /c d/") == ("/a b/", "/c d/")
        assert _parse_cross_queries("/^fi/ /^wa/") == ("/^fi/", "/^wa/")

    def test_parse_two_elements_no_bare_plus(self):
        from infinite_craft_cli.cli import _parse_two_elements
        assert _parse_two_elements("C+++Fire") is None
        assert _parse_two_elements("C++ Fire") == ("C++", "Fire")

    def test_slash_combine_operator_error(self):
        from infinite_craft_cli.cli import _slash_combine_crawl_operator_error
        err = _slash_combine_crawl_operator_error("Water + Fire", "combine")
        assert err is not None
        assert "positional args" in err
        assert "Water + Fire" in err
        assert "/combine Water Fire" in err

    def test_slash_cross_operator_error(self):
        from infinite_craft_cli.cli import _slash_cross_operator_error
        err = _slash_cross_operator_error("fire* * water*")
        assert err is not None
        assert "positional args" in err
        assert "fire* * water*" in err
        assert "/cross fire* water*" in err

    def test_parse_with_args(self):
        from infinite_craft_cli.cli import _parse_with_args
        assert _parse_with_args("Water fire*") == ("Water", "fire*")

    def test_slash_args_exact_and_spaced(self):
        from infinite_craft_cli.cli import _slash_args
        assert _slash_args("/with", "/with") == ""
        assert _slash_args("/with Water fire*", "/with") == "Water fire*"
        assert _slash_args("/without", "/with") is None
        assert _slash_args("/crossing", "/cross") is None


class TestClassifyCommandLine:
    @pytest.mark.parametrize(
        "line,expected_kind",
        [
            ("Water + | Fire", "bad+|"),
            ("Water +| Fire", "+|"),
            ("/^fi/ * /^wa/", "*"),
            ("/combine Water + Fire", "combine"),
            ("Water +", "+"),
            ("Water + Fire", "+"),
        ],
    )
    def test_classify_ordering(self, line, expected_kind):
        from infinite_craft_cli.cli import _classify_command_line
        classified = _classify_command_line(line)
        assert classified is not None
        assert classified[0] == expected_kind

    def test_regex_cross_not_slash_command(self):
        from infinite_craft_cli.cli import _classify_command_line, _is_slash_command_attempt
        line = "/^fi/ * /^wa/"
        assert _is_slash_command_attempt(line) is False
        assert _classify_command_line(line) == ("*", line)


class TestDoSearch:
    def test_no_matches(self, mock_storage):
        from infinite_craft_cli.cli import do_search
        result = do_search(mock_storage, "zzz")
        assert "No matches found" in result

    def test_invalid_regex_message(self, mock_storage):
        from infinite_craft_cli.cli import do_search
        result = do_search(mock_storage, "/[invalid/")
        assert "Invalid regex pattern" in result
        assert "No matches found" not in result

    def test_complex_regex_now_no_match_message(self, mock_storage):
        # DIVERGENCES.md ruling 7: no "too complex" gate; "/(a|aa)+/"
        # parses to two literal branches ("(a" / "aa)+") that match
        # nothing among the base elements, so this is now a plain
        # no-match, not an error.
        from infinite_craft_cli.cli import do_search
        result = do_search(mock_storage, "/(a|aa)+/")
        assert "No matches found." in result
        assert "Invalid regex pattern" not in result

    def test_single_match(self, mock_storage):
        from infinite_craft_cli.cli import do_search
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False
            result = do_search(mock_storage, "water")
            assert "Water" in result

    def test_multiple_matches(self, mock_storage_with_extras):
        from infinite_craft_cli.cli import do_search
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False
            result = do_search(mock_storage_with_extras, "fire")
            assert "Fire" in result
            assert "Firewall" in result


class TestDoList:
    def test_lists_all_elements(self, mock_storage):
        from infinite_craft_cli.cli import do_list
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False
            result = do_list(mock_storage)
            assert "4 elements" in result
            assert "Water" in result
            assert "Fire" in result

    def test_empty_discoveries(self):
        from infinite_craft_cli.cli import do_list
        storage = make_mock_storage([])
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False
            result = do_list(storage)
            assert "0 elements" in result
