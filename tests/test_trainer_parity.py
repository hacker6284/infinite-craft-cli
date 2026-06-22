"""Parity checks for browser trainer helpers (no JS harness required).

Mirrors bookmarklet/trainer.js and extension/trainer.js. Keep in sync when
changing either side.
"""

import re
import time
from pathlib import Path

import pytest

from infinite_craft_cli.cli import (
    MATCH_SCAN_BUDGET,
    MAX_QUERY_LENGTH,
    REGEX_ERROR_COMPLEX,
    _classify_command_line,
    _element_matches_pattern,
    _is_local_command,
    _is_slash_command_attempt,
    _match_elements,
    _parse_cross_queries,
    _parse_query_filter,
    _parse_two_elements,
    _parse_with_args,
    _slash_combine_crawl_operator_error,
    _slash_combine_crawl_pipe_error,
    _slash_cross_operator_error,
    _split_two_positional_args,
    _validate_command_line,
    _validate_query_at_enqueue,
    do_help,
)
from tests.conftest import MockElement, make_mock_storage
from tests.help_utils import (
    assert_help_dual_formats,
    assert_help_query_syntax_once,
    assert_help_text_clean,
    extract_js_help_plaintext,
)

RE_NESTED_QUANTIFIER = re.compile(r"(\+|\*|\?|\{\d*,?\d*\})\s*(\+|\*|\?|\{)")
MAX_REGEX_BODY_LENGTH = 200


def fnmatch_is_safe(pattern: str) -> bool:
    """Python mirror of trainer.js fnmatchIsSafe for documentation testing."""
    if not pattern or len(pattern) > MAX_REGEX_BODY_LENGTH:
        return False
    wildcards = len(re.findall(r"[*?]", pattern))
    if wildcards > 10:
        return False
    if re.search(r"\*{2,}", pattern) or re.search(r"\*.*\*.*\*", pattern):
        return False
    if RE_NESTED_QUANTIFIER.search(pattern):
        return False
    return True


def is_local_command_js_mirror(line: str) -> bool:
    """Python mirror of trainer.js isLocalCommand()."""
    if line in ("/help", "/list", "/history", "/clear"):
        return True
    if line == "/unfilled" or line.startswith("/unfilled "):
        return True
    if line == "/search" or line.startswith("/search "):
        return True
    if line == "/recipe" or line.startswith("/recipe "):
        return True
    return False


def pair_key(a: str, b: str) -> str:
    """Python mirror of trainer.js pairKey()."""
    return "\0".join(sorted([a, b]))


def split_on_first_whitespace_js_mirror(rest: str) -> tuple[str, str] | None:
    rest = rest.strip()
    match = re.search(r"\s", rest)
    if not match:
        return None
    i = match.start()
    first, second = rest[:i].strip(), rest[i:].strip()
    if not first or not second:
        return None
    return first, second


def split_two_positional_args_js_mirror(rest: str) -> tuple[str, str] | None:
    rest = rest.strip()
    if not rest:
        return None
    tokens: list[str] = []
    i = 0
    n = len(rest)
    while i < n and len(tokens) < 2:
        while i < n and rest[i].isspace():
            i += 1
        if i >= n:
            break
        if rest[i] == "/":
            j = rest.find("/", i + 1)
            if j < 0:
                k = i
                while k < n and not rest[k].isspace():
                    k += 1
                token = rest[i:k]
                i = k
            else:
                token = rest[i : j + 1]
                i = j + 1
        else:
            k = i
            while k < n and not rest[k].isspace():
                k += 1
            token = rest[i:k]
            i = k
        token = token.strip()
        if token:
            tokens.append(token)
    if len(tokens) != 2:
        return None
    while i < n and rest[i].isspace():
        i += 1
    if i < n:
        return None
    return tokens[0], tokens[1]


def parse_two_elements_js_mirror(rest: str) -> tuple[str, str] | None:
    return split_on_first_whitespace_js_mirror(rest)


def parse_cross_queries_js_mirror(rest: str) -> tuple[str, str] | None:
    return split_two_positional_args_js_mirror(rest)


def slash_combine_crawl_operator_error_js_mirror(rest: str, kind: str) -> str | None:
    if " + " not in rest:
        return None
    parts = rest.split(" + ", 1)
    positional = f"/{kind} {parts[0].strip()} {parts[1].strip()}"
    return f"positional:{rest.strip()}:{positional}"


def slash_cross_operator_error_js_mirror(rest: str) -> str | None:
    if " * " not in rest:
        return None
    parts = rest.split(" * ", 1)
    positional = f"/cross {parts[0].strip()} {parts[1].strip()}"
    return f"positional:{rest.strip()}:{positional}"


API_SLASH_COMMANDS = (
    "/permute", "/permutate", "/import", "/fill", "/prune", "/export",
    "/exhaust", "/combine", "/crawl", "/with", "/cross",
)


def slash_args_js_mirror(line: str, command: str) -> str | None:
    if line == command:
        return ""
    prefix = command + " "
    if line.startswith(prefix):
        return line[len(prefix):]
    return None


def is_slash_command_attempt_js_mirror(line: str) -> bool:
    if not line.startswith("/"):
        return False
    if re.match(r"/[^/]+/", line):
        return False
    return bool(re.match(r"/\w", line))


def classify_command_line_js_mirror(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if not line:
        return None
    for cmd in API_SLASH_COMMANDS:
        rest = slash_args_js_mirror(line, cmd)
        if rest is not None:
            return cmd.lstrip("/"), rest
    if is_slash_command_attempt_js_mirror(line):
        return None
    if re.search(r"\+\s+\|", line):
        return "bad+|", line
    if " ++ " in line:
        return "++", line
    if "+|" in line:
        return "+|", line
    if " * " in line:
        return "*", line
    if " + " in line or re.search(r" \+$", line.rstrip()):
        return "+", line
    return None


def slash_combine_crawl_pipe_error_js_mirror(rest: str) -> str | None:
    if re.search(r"\+\s+\|", rest):
        return "pipe"
    parsed = parse_two_elements_js_mirror(rest)
    if parsed and parsed[1].startswith("|"):
        return "pipe"
    return None


def normalize_validation_message(msg: str | None) -> str | None:
    """Strip ANSI codes so Python and JS mirror messages compare equal."""
    if msg is None:
        return None
    return re.sub(r"\x1b\[[0-9;]*m", "", msg)


def validate_command_line_js_mirror(line: str) -> str | None:
    """Python mirror of trainer.js validateCommandLine() — plaintext, no ANSI/HTML."""
    classified = classify_command_line_js_mirror(line)
    if not classified:
        if is_slash_command_attempt_js_mirror(line):
            cmd = line.strip().split()[0]
            return f"  Unknown command: {cmd}. Type /help for commands."
        return "  Unknown input. Type /help for commands."
    kind, payload = classified
    if kind == "bad+|":
        return (
            "  Use <element> +| <query> "
            "(no space between + and |). Type /help for commands."
        )
    if kind in ("permute", "permutate", "exhaust"):
        if not payload.strip():
            return f"  Usage: /{kind} <query>"
        return _validate_query_at_enqueue(payload.strip())
    if kind == "import":
        if not payload.strip():
            return "  Usage: /import <element>"
        return None
    if kind in ("export", "fill", "prune"):
        return None
    if kind in ("combine", "crawl"):
        pipe_err = _slash_combine_crawl_pipe_error(payload)
        if pipe_err:
            return normalize_validation_message(pipe_err)
        op_err = _slash_combine_crawl_operator_error(payload, kind)
        if op_err:
            return normalize_validation_message(op_err)
        if not parse_two_elements_js_mirror(payload):
            return f"  Usage: /{kind} <element> <element>"
        return None
    if kind == "with":
        parsed = _parse_with_args(payload)
        if parsed is None:
            return "  Usage: /with <element> <query>"
        return _validate_query_at_enqueue(parsed[1])
    if kind == "cross":
        op_err = _slash_cross_operator_error(payload)
        if op_err:
            return normalize_validation_message(op_err)
        parsed = parse_cross_queries_js_mirror(payload)
        if parsed is None:
            return "  Usage: /cross <query> <query>"
        left_err = _validate_query_at_enqueue(parsed[0])
        if left_err:
            return left_err
        return _validate_query_at_enqueue(parsed[1])
    if kind == "++":
        parts = payload.split(" ++ ", 1)
        if not parts[0].strip() or not parts[1].strip():
            return "  Usage: <element> ++ <element>"
        return None
    if kind == "+|":
        parts = payload.split("+|", 1)
        if not parts[0].strip() or not parts[1].strip():
            return "  Usage: <element> +| <query>"
        return _validate_query_at_enqueue(parts[1].strip())
    if kind == "*":
        parts = payload.split(" * ", 1)
        if not parts[0].strip() or not parts[1].strip():
            return "  Usage: <query> * <query>"
        left_err = _validate_query_at_enqueue(parts[0].strip())
        if left_err:
            return left_err
        return _validate_query_at_enqueue(parts[1].strip())
    if kind == "+":
        if " + " in payload:
            parts = payload.split(" + ", 1)
        else:
            parts = [payload.rsplit(" +", 1)[0], ""]
        if not parts[0].strip() or not parts[1].strip():
            return "  Usage: <element> + <element>"
        return None
    return None


def parse_query_filter_js_mirror(query: str) -> dict[str, str | bool]:
    """Python mirror of trainer.js parseQueryFilter()."""
    q = query.strip()
    exclude = False
    only_new = False
    if q.startswith("!"):
        exclude = True
        q = q[1:]
    elif q.startswith("^"):
        only_new = True
        q = q[1:]
    return {"pattern": q, "exclude": exclude, "onlyNew": only_new}


def match_elements_js_mirror(storage, query: str) -> tuple[list[str], str | None]:
    """Python mirror of trainer.js matchElements()."""
    if len(query) > MAX_QUERY_LENGTH:
        return [], f"Query too long (max {MAX_QUERY_LENGTH} characters)"

    discoveries = list(storage.get_all())
    parsed = parse_query_filter_js_mirror(query)
    pattern = str(parsed["pattern"])
    exclude = bool(parsed["exclude"])
    only_new = bool(parsed["onlyNew"])

    if not pattern.strip():
        if exclude:
            return [e.name for e in discoveries], None
        if only_new:
            return [e.name for e in discoveries if e.is_first_discovery], None
        return [], None

    matches: list[str] = []
    match_error: str | None = None
    deadline = time.monotonic() + MATCH_SCAN_BUDGET
    for e in discoveries:
        if time.monotonic() > deadline:
            return [], REGEX_ERROR_COMPLEX
        matched, err = _element_matches_pattern(e.name, pattern)
        if err:
            match_error = err
            break
        if exclude:
            if not matched:
                matches.append(e.name)
        elif matched:
            matches.append(e.name)
    if match_error:
        return [], match_error
    if only_new:
        return [
            name
            for name in matches
            if next(e for e in discoveries if e.name == name).is_first_discovery
        ], None
    return matches, None


def exhaust_pairs(matches: list[str], all_names: list[str]) -> list[tuple[str, str]]:
    """Python mirror of trainer.js doExhaust pair deduplication."""
    seen: set[str] = set()
    pairs: list[tuple[str, str]] = []
    for target in matches:
        for other in all_names:
            if other == target:
                continue
            key = pair_key(target, other)
            if key in seen:
                continue
            seen.add(key)
            pairs.append((target, other))
    return pairs


class TestFnmatchSafetyParity:
    def test_simple_wildcard_allowed(self):
        assert fnmatch_is_safe("fire*")

    def test_many_wildcards_rejected(self):
        assert not fnmatch_is_safe("*" * 11 + "a")

    def test_consecutive_stars_rejected(self):
        assert not fnmatch_is_safe("a**b")

    def test_triple_star_pattern_rejected(self):
        assert not fnmatch_is_safe("*a*b*c*d*e*f*")


class TestIsLocalCommandParity:
    @pytest.mark.parametrize(
        "line",
        [
            "/help",
            "/list",
            "/history",
            "/clear",
            "/unfilled",
            "/unfilled extra",
            "/search",
            "/search water",
            "/recipe",
            "/recipe Fire",
            "/combine Water + Fire",
            "/fill",
            "/prune",
            "/permutate w*",
            "Water + Fire",
        ],
    )
    def test_matches_cli_and_js_mirror(self, line):
        assert _is_local_command(line) == is_local_command_js_mirror(line)


class TestQueryFilterParity:
    @pytest.mark.parametrize(
        "query,expected_pattern,expected_exclude,expected_only_new",
        [
            ("!fire*", "fire*", True, False),
            ("!", "", True, False),
            ("!  ", "", True, False),
            ("^fire*", "fire*", False, True),
            ("^", "", False, True),
            ("/^fi/", "/^fi/", False, False),
            ("water", "water", False, False),
        ],
    )
    def test_parse_query_filter_matches_js_mirror(
        self, query, expected_pattern, expected_exclude, expected_only_new
    ):
        pattern, exclude, only_new = _parse_query_filter(query)
        mirror = parse_query_filter_js_mirror(query)
        assert pattern == expected_pattern
        assert exclude == expected_exclude
        assert only_new == expected_only_new
        assert mirror == {
            "pattern": expected_pattern,
            "exclude": expected_exclude,
            "onlyNew": expected_only_new,
        }


class TestMatchElementsParity:
    @pytest.fixture
    def parity_storage(self):
        return make_mock_storage([
            MockElement("Water", "💧"),
            MockElement("Fire", "🔥"),
            MockElement("Wind", "🌬️"),
            MockElement("Earth", "🌍"),
            MockElement("Firewall", "🧱", is_first_discovery=True),
        ])

    @pytest.mark.parametrize(
        "query,expected_names",
        [
            ("!fire*", {"Water", "Wind", "Earth"}),
            ("!", {"Water", "Fire", "Wind", "Earth", "Firewall"}),
            ("!/wall/", {"Water", "Fire", "Wind", "Earth"}),
            ("^fire*", {"Firewall"}),
            ("/^fi/", {"Fire", "Firewall"}),
        ],
    )
    def test_match_elements_matches_js_mirror(self, parity_storage, query, expected_names):
        cli_matches, cli_err = _match_elements(parity_storage, query)
        mirror_names, mirror_err = match_elements_js_mirror(parity_storage, query)
        assert cli_err == mirror_err
        assert {e.name for e in cli_matches} == expected_names
        assert set(mirror_names) == expected_names


ROOT = Path(__file__).resolve().parent.parent


class TestTrainerSourceParity:
    def test_bookmarklet_extension_trainer_identical(self):
        bookmarklet = (ROOT / "bookmarklet" / "trainer.js").read_text(encoding="utf-8")
        extension = (ROOT / "extension" / "trainer.js").read_text(encoding="utf-8")
        assert bookmarklet == extension

    def test_wait_for_input_uses_try_enqueue(self):
        source = (ROOT / "bookmarklet" / "trainer.js").read_text(encoding="utf-8")
        assert "function tryEnqueue(line)" in source
        assert "tryEnqueue(val)" in source
        handler = re.search(
            r"function waitForInput\(\)[\s\S]*?function handler\(e\)[\s\S]*?\n      \}",
            source,
        )
        assert handler is not None
        assert "enqueueCommand(val)" not in handler.group(0)


class TestParseHelpersParity:
    @pytest.mark.parametrize(
        "rest,expected",
        [
            ("Water Fire", ("Water", "Fire")),
            ("Water + Fire", None),
            ("Water+Fire", None),
            ("fire* water*", ("fire*", "water*")),
            ("/a b/ /c d/", ("/a b/", "/c d/")),
            ("fire", None),
        ],
    )
    def test_parse_two_and_cross_match_js_mirror(self, rest, expected):
        if expected is None:
            assert _parse_two_elements(rest) == parse_two_elements_js_mirror(rest)
            assert _parse_cross_queries(rest) == parse_cross_queries_js_mirror(rest)
        elif rest.startswith("/") or " " in rest and "*" in rest:
            assert _parse_cross_queries(rest) == expected
            assert parse_cross_queries_js_mirror(rest) == expected
            assert _split_two_positional_args(rest) == expected
            assert split_two_positional_args_js_mirror(rest) == expected
        else:
            assert _parse_two_elements(rest) == expected
            assert parse_two_elements_js_mirror(rest) == expected


class TestClassifyCommandLineParity:
    @pytest.mark.parametrize(
        "line",
        [
            "Water + | Fire",
            "Water +| Fire",
            "/^fi/ * /^wa/",
            "/combine Water + Fire",
            "/combine Water + | Fire",
            "Water +",
            "Water + Fire",
        ],
    )
    def test_classify_matches_js_mirror(self, line):
        assert _classify_command_line(line) == classify_command_line_js_mirror(line)

    def test_regex_cross_not_slash_attempt(self):
        line = "/^fi/ * /^wa/"
        assert _is_slash_command_attempt(line) == is_slash_command_attempt_js_mirror(line)
        assert _is_slash_command_attempt(line) is False


class TestValidateCommandLineParity:
    @pytest.mark.parametrize(
        "line,expect_error",
        [
            ("/combine Water Fire", False),
            ("/crawl Water Fire", False),
            ("/cross Water Fire", False),
            ("/combine Water + Fire", True),
            ("/cross fire* * water*", True),
            ("Water + | Fire", True),
            ("/combine Water + | Fire", True),
            ("Water +", True),
            ("Water + Fire", False),
            ("/^fi/ * /^wa/", False),
            ("/cross /^fi/ /^wa/", False),
            ("/^fi/", True),
            ("/notacommand", True),
            ("Water Fire", True),
            ("/permute", True),
            ("/with Water", True),
            ("Water ++", True),
            ("/import", True),
        ],
    )
    def test_validation_error_agreement(self, line, expect_error):
        cli_err = _validate_command_line(line)
        js_err = validate_command_line_js_mirror(line)
        assert (cli_err is not None) == expect_error
        assert (js_err is not None) == expect_error

    @pytest.mark.parametrize(
        "line",
        [
            "/combine Water + Fire",
            "/cross fire* * water*",
            "Water + | Fire",
            "/combine Water + | Fire",
            "Water +",
            "/^fi/",
            "/notacommand",
            "Water Fire",
            "/crawl Banana",
        ],
    )
    def test_validation_message_agreement(self, line):
        cli_err = normalize_validation_message(_validate_command_line(line))
        js_err = validate_command_line_js_mirror(line)
        assert cli_err == js_err


class TestHelpTextParity:
    def test_python_and_js_help_structure(self):
        py_help = do_help()
        js_help = extract_js_help_plaintext()
        assert_help_text_clean(py_help)
        assert_help_text_clean(js_help)
        assert_help_dual_formats(py_help)
        assert_help_dual_formats(js_help)
        assert_help_query_syntax_once(py_help)
        assert_help_query_syntax_once(js_help)


class TestPairDedupParity:
    def test_symmetric_pairs_deduped(self):
        pairs = exhaust_pairs(["Water", "Wind"], ["Water", "Wind", "Fire", "Earth"])
        keys = {pair_key(a, b) for a, b in pairs}
        assert len(pairs) == len(keys)
        assert ("Water", "Wind") in pairs or ("Wind", "Water") in pairs
        assert len(pairs) == 5

    def test_pair_key_symmetry(self):
        assert pair_key("Water", "Fire") == pair_key("Fire", "Water")


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))