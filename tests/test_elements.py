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

    def test_empty_regex_matches_all(self, mock_storage_with_extras):
        """owner ruling 2026-07-22 (R4): an empty regex body ("//") matches
        every element, not zero."""
        from infinite_craft_cli.cli import _match_elements
        matches, err = _match_elements(mock_storage_with_extras, "//")
        assert err is None
        assert len(matches) == len(mock_storage_with_extras.get_all())


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

    def test_nested_quantifier_group_matches_names_with_a(self, mock_storage_with_extras):
        # DIVERGENCES.md ruling 7: regex.sudo is a full NFA — "(" / ")" are
        # real (always non-capturing) grouping metacharacters, and a
        # postfix quantifier applies to the whole preceding group. "/(a+)+/"
        # is "one-or-more of (one-or-more 'a')", which reduces to "at least
        # one contiguous run of 'a'"; unanchored + case-insensitive search
        # (see is_delimited_regex's regex_search(..., true) call) means it
        # matches any fixture name that contains an 'a' anywhere.
        from infinite_craft_cli.cli import _match_elements
        matches, err = _match_elements(mock_storage_with_extras, "/(a+)+/")
        assert err is None
        names = {e.name for e in matches}
        assert names == {"Water", "Earth", "Steam", "Lava", "Waterfall", "Firewall"}
        # No 'a' in these — must be excluded.
        assert "Fire" not in names
        assert "Wind" not in names
        assert "Mud" not in names
        assert "Dust" not in names

    def test_alternation_quantifier_group_matches_names_with_a(self, mock_storage_with_extras):
        # DIVERGENCES.md ruling 7: "|" inside a group is real nested
        # alternation, and "(...)+" quantifies the whole group. "/(a|aa)+/"
        # is "one-or-more of ('a' OR 'aa')" — since the single-'a' branch
        # alone already matches any run of 'a', this matches the same set
        # of fixture names as the nested-quantifier case above (any name
        # containing 'a').
        from infinite_craft_cli.cli import _match_elements
        matches, err = _match_elements(mock_storage_with_extras, "/(a|aa)+/")
        assert err is None
        names = {e.name for e in matches}
        assert names == {"Water", "Earth", "Steam", "Lava", "Waterfall", "Firewall"}
        assert "Fire" not in names
        assert "Wind" not in names

    def test_non_capturing_group_syntax_is_unsupported(self, mock_storage_with_extras):
        # DIVERGENCES.md ruling 7: regex.sudo's groups are ALWAYS
        # non-capturing (there are no capture slots at all — see the
        # stdlib module header), so there is no dedicated "(?:...)"
        # group-modifier syntax the way PCRE/python `re` have one. Inside
        # a group body, a leading '?' with no preceding atom to quantify
        # is a parse error ("quantifier with nothing to repeat"), so
        # "/(a|(?:aa))+b/" fails to compile — this is a real, confirmed
        # syntax gap (not a matching-correctness bug): writing a bare
        # "(aa)" gets you the same (already non-capturing) grouping that
        # "(?:aa)" would provide elsewhere, but the "?:" spelling itself
        # is not recognized.
        from infinite_craft_cli.cli import _match_elements
        matches, err = _match_elements(mock_storage_with_extras, "/(a|(?:aa))+b/")
        assert matches == []
        assert err == "Invalid regex pattern"

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

    def test_complex_regex_matches_base_elements_with_a(self, mock_storage):
        # DIVERGENCES.md ruling 7: no "too complex" gate, and "/(a|aa)+/"
        # is a real quantified alternation group meaning "one-or-more of
        # ('a' OR 'aa')", i.e. "contains a run of 'a'". Of the default
        # base elements (Water, Fire, Wind, Earth), Water and Earth
        # contain 'a'; Fire and Wind do not.
        from infinite_craft_cli.cli import do_search
        result = do_search(mock_storage, "/(a|aa)+/")
        assert "No matches found." not in result
        assert "Invalid regex pattern" not in result
        assert "Water" in result
        assert "Earth" in result
        assert "Fire" not in result
        assert "Wind" not in result

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
