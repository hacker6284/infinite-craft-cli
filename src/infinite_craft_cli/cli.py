#!/usr/bin/env python3
"""Infinite Craft CLI — combine elements from the terminal or as a scripted tool."""

import asyncio
import argparse
import builtins
import contextlib
import fnmatch
import gzip
import re
import json
import os
import select
import shutil
import signal
import sys
import tempfile
import threading
import time
from collections.abc import Callable

try:
    import termios
    import tty
except ImportError:
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]

try:
    import readline  # noqa: F401 — enables arrow keys, history in input()
except ImportError:
    pass  # readline not available on Windows

from infinite_craft_cli.element import Element
from infinite_craft_cli.client import (
    InfiniteCraftClient,
    fetch_json,
    _get_sync_session,
)
from infinite_craft_cli.ratelimit import RateLimitCancelled
from infinite_craft_cli.storage import DiscoveryStorage
from infinite_craft_cli import __version__

from infinite_craft_cli.data import DISCOVERIES_PATH, RECIPES_PATH, EXPORT_PATH

# ---------------------------------------------------------------------------
# ANSI colors
# ---------------------------------------------------------------------------
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
RED = "\033[31m"

API_RATE_LIMIT = 60  # requests per minute — conservative to avoid Cloudflare blocks
API_CONCURRENCY = 2  # parallel workers for bulk operations
MAX_QUERY_LENGTH = 512
MAX_REGEX_BODY_LENGTH = 200
_MAX_IC_COMPRESSED_BYTES = 32 * 1024 * 1024
_MAX_IC_DECOMPRESSED_BYTES = 64 * 1024 * 1024
_MAX_IC_ITEMS = 50_000
_RATE_LIMIT_SLEEP_STEP = 0.05
REGEX_TIMEOUT = 0.02
MATCH_SCAN_BUDGET = 0.5
REGEX_ERROR_INVALID = "Invalid regex pattern"
REGEX_ERROR_COMPLEX = "Regex pattern too complex"
_QUERY_HELP = "Search query (wildcards, /regex/, ! exclude, ^ first discoveries)"

import regex as _regex_module

_RE_NESTED_QUANTIFIER = re.compile(r"(\+|\*|\?|\{\d*,?\d*\})\s*(\+|\*|\?|\{)")
_RE_DELIMITED_REGEX = re.compile(r"/[^/]+/")

# Session-only history
_history: list[tuple[str, str, str]] = []
_session_input_history: list[str] = []

# Interactive command queue (API/long-running commands)
_command_queue: list[str] = []
_current_command: str | None = None
_api_worker_task: asyncio.Task | None = None
_MAX_QUEUE_DEPTH = 50
_MAX_PERMUTATE_ROUNDS = 50
_stdin_lock = asyncio.Lock()
_cancel_scope_depth = 0
_sigint_previous: object | None = None
_confirm_future: asyncio.Future[str] | None = None
_confirm_answer_buffer: str | None = None
_last_queue_snapshot: str = ""
_queue_panel_height: int = 0
_interactive_mode_active: bool = False
_confirm_expected: bool = False
_bulk_confirm_pending: bool = False
_bulk_confirm_resolved: bool = True
_BULK_CONFIRM_COMMANDS = frozenset(
    {"/permutate", "/permute", "/exhaust", "/cross", "/with", "/crawl"}
)

# TTY chrome: streaming output scrolls above a pinned queue + prompt (trainer.js layout).
_chrome_enabled: bool = False
_chrome_prompt: str = ""
_chrome_input_active: bool = False
_chrome_partial: str = ""
_chrome_last_reserve: int = 0
_repl_print_patched: bool = False
_repl_print_lock = threading.RLock()
_chrome_last_state: object = None
_tty_stdin_unread: list[str] = []
# Total time budget to collect CSI/SS3 bytes after ESC before lone-Escape (skip).
_ESC_SEQUENCE_WAIT_S = 0.3
_CSI_POLL_INTERVAL_S = 0.02
_builtin_print = builtins.print

# Test-only seams (set by harness in tests; never used in production).
# Allows deterministic input without real TTY/termios/select, and avoids races.
_tty_read_byte_hook: Callable[[float], str | None] | None = None
_test_prompt_input_hook: Callable[[str], str] | None = None


def _is_test_context() -> bool:
    """Centralized cheap guard for test seams (avoids duplication)."""
    return "pytest" in sys.modules or "PYTEST_CURRENT_TEST" in os.environ


def _reset_test_state() -> None:
    """Reset all mutable interactive/TTY/chrome/queue state for test isolation.

    Call this from test fixtures and finally blocks. Not for production use.
    """
    global _pair_cache, _history, _session_input_history, _command_queue
    global _current_command, _api_worker_task, _confirm_future, _confirm_answer_buffer
    global _last_queue_snapshot, _queue_panel_height, _interactive_mode_active
    global _confirm_expected, _bulk_confirm_pending, _bulk_confirm_resolved
    global _chrome_enabled, _chrome_prompt, _chrome_input_active, _chrome_partial
    global _chrome_last_reserve, _chrome_last_state, _repl_print_patched
    global _tty_stdin_unread, _cancelled, _cancel_scope_depth
    global _discard_queue_after_cancel, _skip_summary_shown, _sigint_previous
    global _tty_read_byte_hook, _test_prompt_input_hook
    _pair_cache.clear()
    _history.clear()
    _session_input_history.clear()
    _command_queue.clear()
    _current_command = None
    _confirm_future = None
    _confirm_answer_buffer = None
    _last_queue_snapshot = ""
    _queue_panel_height = 0
    _interactive_mode_active = False
    _confirm_expected = False
    _bulk_confirm_pending = False
    _bulk_confirm_resolved = True
    _chrome_enabled = False
    _chrome_prompt = ""
    _chrome_input_active = False
    _chrome_partial = ""
    _chrome_last_reserve = 0
    _chrome_last_state = None
    if _repl_print_patched:
        try:
            _patch_repl_print(False)
        except Exception:
            _repl_print_patched = False
    _tty_stdin_unread = []
    _cancelled = False
    _cancel_scope_depth = 0
    _discard_queue_after_cancel = False
    _skip_summary_shown = False
    _sigint_previous = None
    _tty_read_byte_hook = None
    _test_prompt_input_hook = None
    # ensure no stray worker (cancel before null to avoid leak)
    if (
        _api_worker_task is not None
        and not getattr(_api_worker_task, "done", lambda: True)()
    ):
        try:
            _api_worker_task.cancel()
        except Exception:
            pass
    _api_worker_task = None


# ---------------------------------------------------------------------------
# Persistent recipe store
# ---------------------------------------------------------------------------
class RecipeStoreError(Exception):
    """recipes.json is missing or unreadable."""


class CommandCancelled(Exception):
    """A queued command was skipped (Escape) before it finished."""


def _load_recipes() -> dict[str, list[list[str]]]:
    """Load recipes.json: {result_name: [[a_name, b_name], ...]}"""
    if not os.path.exists(RECIPES_PATH):
        return {}
    try:
        with open(RECIPES_PATH, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise RecipeStoreError(
            f"recipes.json is corrupted ({e}). "
            f"Back up {RECIPES_PATH}, repair or delete it, then retry."
        ) from e


def _save_recipes(recipes: dict[str, list[list[str]]]):
    dir_name = os.path.dirname(RECIPES_PATH) or "."
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(recipes, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, RECIPES_PATH)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def _record_recipe(result_name: str, a_name: str, b_name: str):
    """Record that a_name + b_name = result_name."""
    _record_recipes_batch([(result_name, a_name, b_name)])


def _record_recipes_batch(entries: list[tuple[str, str, str]]):
    """Record multiple recipes with a single disk write."""
    if not entries:
        return
    recipes = _load_recipes()
    changed = False
    for result_name, a_name, b_name in entries:
        pair = sorted([a_name, b_name])
        if result_name not in recipes:
            recipes[result_name] = []
        if pair not in recipes[result_name]:
            recipes[result_name].append(pair)
            changed = True
    if changed:
        _save_recipes(recipes)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------
def _sanitize_queue_line(line: str) -> str:
    """Strip control characters from queue display (keep emoji and other printable Unicode)."""
    return "".join(
        c for c in line if (c.isprintable() and c not in "\x00\x07\x1b") or c == " "
    )


def _color(text: str, code: str) -> str:
    if sys.stdout.isatty():
        return f"{code}{text}{RESET}"
    return text


def _sanitize_tty_text(text: str) -> str:
    """Strip control characters from untrusted text before TTY output."""
    return _sanitize_queue_line(text)


def _tty(text: str) -> str:
    """Sanitize untrusted text when writing to an interactive terminal."""
    if sys.stdout.isatty():
        return _sanitize_tty_text(text)
    return text


def _sanitize_element_name(name: str) -> str:
    """Normalize API/import element names before storage."""
    return _sanitize_tty_text(name.strip())


def format_element(elem) -> str:
    raw = str(elem)  # uses Element.__str__ which handles emoji
    s = _sanitize_tty_text(raw) if sys.stdout.isatty() else raw
    if elem.is_first_discovery:
        s += " " + _color("[FIRST DISCOVERY!]", BOLD + MAGENTA)
    return s


def format_result(first_name: str, second_name: str, result) -> str:
    if sys.stdout.isatty():
        first_name = _sanitize_tty_text(first_name)
        second_name = _sanitize_tty_text(second_name)
    if result.name is None:
        res = _color("Nothing", DIM)
    else:
        res = format_element(result)
    return f"  {first_name} + {second_name} = {res}"


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------
def _resolve_element(storage, name: str):
    """Look up an element by name in discoveries; fall back to bare Element."""
    found = storage.get_by_name(name)
    if found is not None:
        return found
    # Also try title-cased version
    title = name.strip().title()
    if title != name:
        found = storage.get_by_name(title)
        if found is not None:
            return found
    return Element(name=name.strip().title())


# Runtime cache for pair results — avoids re-hitting the API for the same combo
_pair_cache: dict[tuple[str, str], Element] = {}


def _raise_if_cancelled() -> None:
    if _cancelled:
        raise CommandCancelled()


async def _cached_pair(client, storage, a, b):
    """Wrapper around client.pair that caches results by sorted element names."""
    _raise_if_cancelled()
    key = tuple(sorted([a.name, b.name]))
    if key in _pair_cache:
        return _pair_cache[key]
    for attempt in range(3):
        _raise_if_cancelled()
        try:
            result = await client.pair(a.name, b.name)
            break
        except RateLimitCancelled:
            raise CommandCancelled() from None
        except Exception:
            if attempt == 2:
                raise
            if await _sleep_cancellable_async(2**attempt):
                raise CommandCancelled()
    _pair_cache[key] = result
    if result.name is not None:
        _record_recipe(result.name, a.name, b.name)
    return result


async def do_combine(client, storage, first_name: str, second_name: str) -> str:
    first = _resolve_element(storage, first_name)
    second = _resolve_element(storage, second_name)
    try:
        result = await _cached_pair(client, storage, first, second)
    except CommandCancelled:
        raise
    except Exception as e:
        return _color(f"  Error: {_tty(str(e))}", RED)
    # If the pairing succeeded, ensure both inputs and result are in discoveries
    if result.name is not None:
        for elem in (first, second):
            storage.add(
                name=_sanitize_element_name(elem.name),
                emoji=elem.emoji,
                is_first_discovery=False,
            )
        storage.add(
            name=_sanitize_element_name(result.name),
            emoji=result.emoji,
            is_first_discovery=result.is_first_discovery,
        )
    result_display = result.name if result.name else "Nothing"
    _history.append((first_name.strip(), second_name.strip(), result_display))
    return format_result(str(first), str(second), result)


def _slash_args(line: str, command: str) -> str | None:
    """Return arguments after a slash command, or None if the line is not that command."""
    if line == command:
        return ""
    prefix = command + " "
    if line.startswith(prefix):
        return line[len(prefix) :]
    return None


def _parse_query_filter(query: str) -> tuple[str, bool, bool]:
    """Parse a query, returning (pattern, exclude, only_new).

    Prefix ``!`` excludes matching elements (negation).
    Prefix ``^`` limits results to first discoveries among pattern matches.
    """
    q = query.strip()
    exclude = False
    only_new = False
    if q.startswith("!"):
        exclude = True
        q = q[1:]
    elif q.startswith("^"):
        only_new = True
        q = q[1:]
    return q, exclude, only_new


def _is_delimited_regex(pattern: str) -> bool:
    pattern = pattern.strip()
    return len(pattern) >= 2 and pattern.startswith("/") and pattern.endswith("/")


def _contains_delimited_regex(text: str) -> bool:
    return _RE_DELIMITED_REGEX.search(text) is not None


def _regex_is_safe(regex_body: str) -> bool:
    if not regex_body or len(regex_body) > MAX_REGEX_BODY_LENGTH:
        return False
    # Alternation is not supported — nested groups bypass simpler safety checks.
    if "|" in regex_body:
        return False
    if _RE_NESTED_QUANTIFIER.search(regex_body):
        return False
    # Reject grouped quantifiers followed by quantifiers, e.g. (a+)+ or (a*)*
    if re.search(r"\([^)]*[+*?][^)]*\)[+*?{]", regex_body):
        return False
    return True


def _regex_search(pattern: str, name: str) -> tuple[bool | None, str | None]:
    """Search with regex. Returns (matched, error_message)."""
    if not _regex_is_safe(pattern):
        return None, REGEX_ERROR_COMPLEX
    try:
        found = _regex_module.search(
            pattern, name, _regex_module.IGNORECASE, timeout=REGEX_TIMEOUT
        )
        return (found is not None), None
    except TimeoutError:
        return None, REGEX_ERROR_COMPLEX
    except _regex_module.error:
        return None, REGEX_ERROR_INVALID


def _element_matches_pattern(name: str, pattern: str) -> tuple[bool, str | None]:
    """Match an element name against a query pattern."""
    pattern = pattern.strip()
    if not pattern:
        return False, None
    if _is_delimited_regex(pattern):
        regex_body = pattern[1:-1]
        if not regex_body:
            return False, None
        matched, err = _regex_search(regex_body, name)
        if err:
            return False, err
        return matched, None
    name_lower = name.lower()
    pattern_lower = pattern.lower()
    if any(c in pattern_lower for c in "*?[]"):
        return fnmatch.fnmatch(name_lower, pattern_lower), None
    return pattern_lower in name_lower, None


def _match_elements(storage, query: str) -> tuple[list[Element], str | None]:
    """Return (matches, error_message) for discovered elements matching a query."""
    if len(query) > MAX_QUERY_LENGTH:
        return [], f"Query too long (max {MAX_QUERY_LENGTH} characters)"
    discoveries = storage.get_all()
    q, exclude, only_new = _parse_query_filter(query)
    if not q.strip():
        if exclude:
            return list(discoveries), None
        if only_new:
            return [e for e in discoveries if e.is_first_discovery], None
        return [], None
    matches: list[Element] = []
    match_error: str | None = None
    deadline = time.monotonic() + MATCH_SCAN_BUDGET
    for e in discoveries:
        if time.monotonic() > deadline:
            return [], REGEX_ERROR_COMPLEX
        matched, err = _element_matches_pattern(e.name, q)
        if err:
            match_error = err
            break
        if exclude:
            if not matched:
                matches.append(e)
        elif matched:
            matches.append(e)
    if match_error:
        return [], match_error
    if only_new:
        matches = [e for e in matches if e.is_first_discovery]
    return matches, None


def do_search(storage, query: str) -> str:
    matches, err = _match_elements(storage, query)
    if err:
        return f"  {err}"
    if not matches:
        return "  No matches found."
    return "\n".join(f"  {format_element(e)}" for e in matches)


def do_recipe(storage, name: str) -> str:
    """Show shortest recipe tree for an element via BFS on local recipes."""
    recipes = _load_recipes()
    target = name.strip()

    # Find exact match in discoveries
    elem = storage.get_by_name(target)
    if elem is None:
        elem = storage.get_by_name(target.title())
    if elem is None:
        return f"  {format_element(Element(name=target))} not found in discoveries."
    target = elem.name

    if target in _BASE_ELEMENTS:
        return f"  {format_element(Element(name=target))} is a base element."

    if target not in recipes or not recipes.get(target):
        return f"  No recipe known for {format_element(Element(name=target))}. Try /fill or /import."

    # BFS to find all elements needed, tracking shortest path
    # parent[name] = (a_name, b_name) that produces it
    # Terminals (elements with no recipe entry or empty recipe list,
    # introduced by /fill or /import) are treated as additional roots so
    # that lineages with unmakeable constituents can still be traced.
    parent = {}
    visited = set(_BASE_ELEMENTS)
    found = False

    def _is_available(n: str) -> bool:
        # A name is available (usable as input without further crafting in
        # this layer) if already visited, a base, or has no (truthy) recipe
        # entry. The latter treats both absent keys and empty lists as
        # terminals (constituents that cannot be made via known recipes).
        return n in visited or n in _BASE_ELEMENTS or not recipes.get(n)

    while not found:
        # Find everything we can make using previously visited elements
        # OR terminal constituents (no recipe of their own).
        new_this_layer = {}
        for result_name, pairs in recipes.items():
            if result_name in visited or result_name in new_this_layer:
                continue
            for pair in pairs:
                a_ok = _is_available(pair[0])
                b_ok = _is_available(pair[1])
                if a_ok and b_ok:
                    new_this_layer[result_name] = (pair[0], pair[1])
                    if result_name == target:
                        found = True
                    break
        if not new_this_layer:
            break
        # Commit entire layer at once
        for name, recipe in new_this_layer.items():
            parent[name] = recipe
            visited.add(name)

    if not found:
        return (
            f"  Cannot trace full lineage for {format_element(Element(name=target))} — missing intermediate recipes."
        )

    # Walk back from target to collect steps in order.
    # Terminals (no parent entry) are treated as resolved leaves with no step.
    steps = []
    to_resolve = [target]
    resolved = set(_BASE_ELEMENTS)
    while to_resolve:
        name = to_resolve.pop()
        if name in resolved:
            continue
        if name not in parent:
            # Terminal leaf (constituent from /fill or /import with no recipe;
            # bases are pre-seeded in resolved/visited and never appear in parent).
            resolved.add(name)
            continue
        a, b = parent[name]
        # Ensure dependencies are resolved first
        for dep in (a, b):
            if dep in resolved:
                continue
            if dep not in parent and dep not in _BASE_ELEMENTS:
                resolved.add(dep)  # terminal leaf — no step emitted
                continue
            to_resolve.append(name)  # re-queue
            to_resolve.append(dep)
            break
        else:
            steps.append((a, b, name))
            resolved.add(name)

    t_elem = storage.get_by_name(target)
    t_str = format_element(t_elem) if t_elem else _color(format_element(Element(name=target)), BOLD)
    lines = [f"  Recipe for {t_str} ({len(steps)} steps):"]
    for a, b, r in steps:
        a_elem = storage.get_by_name(a)
        b_elem = storage.get_by_name(b)
        r_elem = storage.get_by_name(r)
        a_str = format_element(a_elem) if a_elem else format_element(Element(name=a))
        b_str = format_element(b_elem) if b_elem else format_element(Element(name=b))
        r_str = format_element(r_elem) if r_elem else format_element(Element(name=r))
        lines.append(f"    {a_str} + {b_str} = {r_str}")
    return "\n".join(lines)


def do_list(storage) -> str:
    discoveries = storage.get_all()
    header = f"  Discovered {len(discoveries)} elements:"
    lines = [f"  {format_element(e)}" for e in discoveries]
    return header + "\n" + "\n".join(lines)


def do_history(storage=None) -> str:
    if not _history:
        return "  No combinations tried yet."
    lines = []
    for i, (a, b, r) in enumerate(_history, 1):
        if storage is not None:
            a_elem = _resolve_element(storage, a)
            b_elem = _resolve_element(storage, b)
            if r == "Nothing" or not r:
                r_str = _tty(r)
            else:
                r_elem = _resolve_element(storage, r)
                r_str = format_element(r_elem)
            lines.append(f"  {i}. {format_element(a_elem)} + {format_element(b_elem)} = {r_str}")
        else:
            lines.append(f"  {i}. {_tty(a)} + {_tty(b)} = {_tty(r)}")
    return "\n".join(lines)


async def do_crawl(client, storage, first_name: str, second_name: str):
    """Combine two elements, then iteratively combine results with all inputs until nothing new."""
    first = _resolve_element(storage, first_name)
    second = _resolve_element(storage, second_name)
    pool = {first.name: first, second.name: second}
    tried = set()
    generation = 0

    _repl_print_lines(
        f"  Crawling from {_color(format_element(first), BOLD)} "
        f"and {_color(format_element(second), BOLD)}..."
    )
    _repl_print_lines("  (Ctrl+C to stop)")

    while True:
        if _cancelled:
            if not _skip_summary_shown:
                _repl_print_lines("  Stopped.")
                _mark_cancel_notified()
            break
        generation += 1
        names = sorted(pool.keys())
        new_pairs = []
        for i in range(len(names)):
            for j in range(i, len(names)):
                key = tuple(sorted([names[i], names[j]]))
                if key not in tried:
                    new_pairs.append((pool[names[i]], pool[names[j]]))
                    tried.add(key)

        if not new_pairs:
            _repl_print_lines(f"  Exhausted all pairs. {len(pool)} elements in pool.")
            break

        _repl_print_lines(f"  --- Generation {generation}: {len(new_pairs)} new pairs to try ---")

        # Snapshot pool names before running pairs
        before = set(pool.keys())
        await _combine_pairs(client, storage, new_pairs)

        # Check pair cache for new elements produced this generation
        new_elements = []
        for a, b in new_pairs:
            key = tuple(sorted([a.name, b.name]))
            result = _pair_cache.get(key)
            if result and result.name and result.name not in pool:
                pool[result.name] = result
                new_elements.append(result)

        new_count = len(new_elements)
        _repl_print_lines(f"  +{new_count} new ({len(pool)} in pool)")

        if new_count == 0 or _cancelled:
            if _cancelled:
                if not _skip_summary_shown:
                    _repl_print_lines("  Stopped.")
                    _mark_cancel_notified()
            else:
                _repl_print_lines("  No new discoveries. Stopping.")
            break

    _repl_print_lines(f"  Final pool ({len(pool)}):")
    for name in sorted(pool.keys()):
        _repl_print_lines(f"    {format_element(pool[name])}")


async def do_exhaust(client, storage, query: str):
    """For each element matching query, combine with every discovered element."""
    matches, err = _match_elements(storage, query)
    if err:
        _repl_print_lines(f"  {err}")
        return
    if not matches:
        _repl_print_lines(f"  No elements match: {query}")
        return

    all_elements = list(storage.get_all())
    seen: set[tuple[str, str]] = set()
    pairs: list[tuple] = []
    for target in matches:
        for other in all_elements:
            if other.name == target.name:
                continue
            key = tuple(sorted([target.name, other.name]))
            if key not in seen:
                seen.add(key)
                pairs.append((target, other))
    if not pairs:
        _repl_print_lines(f"  No valid pairs for query: {query}")
        return

    _repl_print_lines(
        f"  Exhausting {len(matches)} element(s) matching {_color(query, YELLOW)} "
        f"with all discoveries ({len(pairs)} pairs)..."
    )
    if len(matches) <= 10:
        for m in matches:
            _repl_print_lines(f"    {format_element(m)}")
    await _confirm_and_run_pairs(client, storage, pairs)


async def do_with(client, storage, element_name: str, query: str):
    """Combine an element with all discoveries matching a query."""
    target = _resolve_element(storage, element_name)
    others, err = _match_elements(storage, query)
    if err:
        _repl_print_lines(f"  {err}")
        return
    if not others:
        _repl_print_lines(f"  No elements match: {query}")
        return
    pairs = [(target, o) for o in others if o.name != target.name]
    if not pairs:
        _repl_print_lines(f"  No other elements match: {query}")
        return
    _repl_print_lines(
        f"  Combining {_color(format_element(target), BOLD)} with {len(pairs)} elements "
        f"matching {_color(query, YELLOW)}..."
    )
    await _confirm_and_run_pairs(client, storage, pairs)


def _slash_combine_crawl_pipe_error(rest: str) -> str | None:
    """Reject slash combine/crawl payloads that use spaced ``+ |`` instead of ``+|``."""
    if re.search(r"\+\s+\|", rest):
        return (
            "  Use <element> +| <query> "
            f"(no space between + and |). Type {_color('/help', YELLOW)} for commands."
        )
    parsed = _parse_two_elements(rest)
    if parsed and parsed[1].startswith("|"):
        return (
            "  Use <element> +| <query> "
            f"(no space between + and |). Type {_color('/help', YELLOW)} for commands."
        )
    return None


def _slash_combine_crawl_operator_error(rest: str, kind: str) -> str | None:
    """Reject slash combine/crawl payloads that still use `` + `` operator syntax."""
    if " + " not in rest:
        return None
    parts = rest.split(" + ", 1)
    positional = f"/{kind} {parts[0].strip()} {parts[1].strip()}"
    return (
        f"  Slash /{kind} uses positional args, not +. "
        f"Try {_color(rest.strip(), YELLOW)} (shorthand) or "
        f"{_color(positional, YELLOW)}."
    )


def _slash_cross_operator_error(rest: str) -> str | None:
    """Reject slash cross payloads that still use `` * `` operator syntax."""
    if " * " not in rest:
        return None
    parts = rest.split(" * ", 1)
    positional = f"/cross {parts[0].strip()} {parts[1].strip()}"
    return (
        "  Slash /cross uses positional args, not *. "
        f"Try {_color(rest.strip(), YELLOW)} (shorthand) or "
        f"{_color(positional, YELLOW)}."
    )


def _split_two_positional_args(rest: str) -> tuple[str, str] | None:
    """Split into two positional args, respecting ``/regex/`` tokens."""
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
                j = i
                while j < n and not rest[j].isspace():
                    j += 1
                token = rest[i:j]
                i = j
            else:
                token = rest[i : j + 1]
                i = j + 1
        else:
            j = i
            while j < n and not rest[j].isspace():
                j += 1
            token = rest[i:j]
            i = j
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


def _parse_two_elements(rest: str) -> tuple[str, str] | None:
    """Parse two element names from positional slash combine/crawl args."""
    rest = rest.strip()
    parts = rest.split(None, 1)
    if len(parts) != 2:
        return None
    first, second = parts[0].strip(), parts[1].strip()
    if not first or not second:
        return None
    return first, second


def _parse_with_args(rest: str) -> tuple[str, str] | None:
    """Parse ``<element> <query>`` for /with."""
    rest = rest.strip()
    parts = rest.split(None, 1)
    if len(parts) != 2:
        return None
    element, query = parts[0].strip(), parts[1].strip()
    if not element or not query:
        return None
    return element, query


def _parse_cross_queries(rest: str) -> tuple[str, str] | None:
    """Parse two positional queries for slash /cross (supports ``/regex/`` tokens)."""
    return _split_two_positional_args(rest)


_BULK_WARN_THRESHOLD = 200


_cancelled = False
_discard_queue_after_cancel = False
_skip_summary_shown = False


def _reset_cancelled():
    global _cancelled, _discard_queue_after_cancel, _skip_summary_shown
    _cancelled = False
    _discard_queue_after_cancel = False
    _skip_summary_shown = False


def _mark_cancel_notified() -> None:
    """Record that the running command already printed a cancel/stop summary."""
    global _skip_summary_shown
    _skip_summary_shown = True


def _request_skip_current() -> bool:
    """Skip the running queued command and continue to the next (Escape)."""
    global _cancelled, _discard_queue_after_cancel
    if _current_command is None and not _waiting_for_confirm():
        return False
    _cancelled = True
    _discard_queue_after_cancel = False
    if _confirm_future is not None and not _confirm_future.done():
        _confirm_future.set_result("")
    _chrome_sync()
    return True


def _on_sigint():
    global _cancelled, _discard_queue_after_cancel
    _cancelled = True
    if _cancel_scope_depth > 0:
        _discard_queue_after_cancel = True


def _enter_cancel_scope():
    """Install SIGINT cancel handler for one top-level queued command."""
    global _cancel_scope_depth, _sigint_previous
    _cancel_scope_depth += 1
    if _cancel_scope_depth == 1:
        loop = asyncio.get_running_loop()
        try:
            _sigint_previous = loop.add_signal_handler(signal.SIGINT, _on_sigint)
        except NotImplementedError:
            _sigint_previous = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, lambda *_: _on_sigint())


def _exit_cancel_scope():
    global _cancel_scope_depth, _sigint_previous
    _cancel_scope_depth -= 1
    if _cancel_scope_depth == 0:
        loop = asyncio.get_running_loop()
        try:
            loop.remove_signal_handler(signal.SIGINT)
        except (NotImplementedError, ValueError):
            if _sigint_previous is not None:
                signal.signal(signal.SIGINT, _sigint_previous)
        _sigint_previous = None


_ANSI_ESCAPE_RE = re.compile(r"\033\[[0-9;]*[ -/]*[@-~]")


def _tty_height() -> int:
    try:
        return max(1, shutil.get_terminal_size().lines)
    except OSError:
        return 24


def _tty_width() -> int:
    try:
        return max(1, shutil.get_terminal_size().columns)
    except OSError:
        return 80


def _ansi_visible_len(text: str) -> int:
    """Terminal column count after stripping ANSI SGR sequences."""
    return len(_ANSI_ESCAPE_RE.sub("", text))


def _fit_visible(text: str, maxw: int) -> str:
    """Truncate to <= maxw visible cols; preserve whole ANSI SGRs (for long names/regex in queue/prompt)."""
    if maxw <= 0 or not text:
        return ""
    if _ansi_visible_len(text) <= maxw:
        return text
    out_parts: list[str] = []
    vis = 0
    i = 0
    while i < len(text):
        m = _ANSI_ESCAPE_RE.match(text, i)
        if m:
            out_parts.append(m.group(0))
            i = m.end()
            continue
        if vis < maxw:
            out_parts.append(text[i])
            vis += 1
        i += 1
    return "".join(out_parts)


def _chrome_write_row(row: int, content: str = "") -> None:
    """Write one pinned chrome row, clearing the full visible width."""
    cols = _tty_width()
    sys.stdout.write(f"\033[{row};1H\033[K")
    if not content:
        return
    content = _fit_visible(content, max(0, cols - 1))
    sys.stdout.write(content)
    sys.stdout.write(RESET)  # close attrs if truncated mid-span (long name/regex)
    visible = _ansi_visible_len(content)
    if visible < cols:
        sys.stdout.write("\033[K")
    else:
        for spill in range(1, (visible - 1) // cols + 1):
            sys.stdout.write(f"\033[{row + spill};1H\033[K")


def _chrome_reserved_lines() -> int:
    """Lines reserved at the bottom for queue panel + prompt.

    For single-item queue (compact path in _format_queue_display), display has 1 line
    so reserved drops to 2 (vs ~4 previously); multi-item uses full. Dynamic via count.
    """
    display = _format_queue_display()
    queue_lines = display.count("\n") + 1 if display else 0
    return max(1, queue_lines + 1)


def _scroll_region_bottom() -> int:
    rows = _tty_height()
    reserve = _chrome_reserved_lines()
    return max(1, rows - reserve)


def _chrome_update_scroll_region(*, reposition: bool = False) -> int:
    """Pin the bottom chrome; return the last line of the scrolling output region."""
    global _chrome_last_reserve
    rows = _tty_height()
    reserve = _chrome_reserved_lines()
    bottom = max(1, rows - reserve)
    if reserve != _chrome_last_reserve:
        if _chrome_last_reserve > reserve:
            # Rows that were chrome are now scrollable; clear stale queue/prompt text.
            clear_from = rows - _chrome_last_reserve + 1
            clear_to = rows - reserve
            for row in range(clear_from, clear_to + 1):
                _chrome_write_row(row)
        sys.stdout.write(f"\033[1;{bottom}r")
        sys.stdout.flush()
        _chrome_last_reserve = reserve
        reposition = True
    if reposition:
        sys.stdout.write(f"\033[{bottom};1H")
        sys.stdout.flush()
    return bottom


def _chrome_active_prompt() -> str:
    """Prompt shown in the pinned chrome row (live while input is active)."""
    if _chrome_input_active:
        return _craft_prompt()
    return _chrome_prompt


def _chrome_state_key() -> tuple:
    return (
        _format_queue_display(),
        _chrome_active_prompt(),
        _chrome_input_active,
        _waiting_for_confirm(),
        _bulk_confirm_pending,
        _current_command,
        tuple(_command_queue),
        _api_worker_task is not None and not _api_worker_task.done()
        if _api_worker_task
        else False,
    )


def _chrome_draw(*, partial: str = "") -> None:
    """Draw queue panel and prompt on fixed rows below the scroll region."""
    if not _chrome_enabled:
        return
    rows = _tty_height()
    scroll_end = _scroll_region_bottom()
    display = _format_queue_display()
    chrome_start = scroll_end + 1

    for row in range(chrome_start, rows + 1):
        _chrome_write_row(row)

    if display:
        for offset, line in enumerate(display.split("\n")):
            _chrome_write_row(chrome_start + offset, line)

    # prompt follows immediately after (shorter for compact single queue line)
    prompt_row = chrome_start + (display.count("\n") + 1 if display else 0)
    prompt_text = _chrome_active_prompt()
    if prompt_text:
        _chrome_write_row(prompt_row, f"{prompt_text}{partial}")
    sys.stdout.flush()


def _chrome_refresh(*, force: bool = False, partial: str | None = None) -> None:
    """Repaint pinned chrome when queue state changes."""
    global _last_queue_snapshot, _chrome_last_state
    if not _chrome_enabled:
        return
    state = _chrome_state_key()
    if not force and state == _chrome_last_state:
        return
    _chrome_last_state = state
    _last_queue_snapshot = _format_queue_display()
    _chrome_update_scroll_region(reposition=True)
    if partial is None and _chrome_input_active:
        partial = _chrome_partial
    _chrome_draw(partial=partial or "")


def _chrome_sync() -> None:
    """Refresh chrome after queue/confirm changes while the user may be mid-input."""
    if not _chrome_enabled:
        return
    with _repl_print_lock:
        _chrome_refresh(force=True)


def _chrome_enable() -> None:
    """Enable pinned queue/prompt chrome (TTY interactive mode only)."""
    global _chrome_enabled, _chrome_last_reserve
    if not sys.stdout.isatty():
        return
    _chrome_enabled = True
    _chrome_last_reserve = 0
    _chrome_update_scroll_region(reposition=True)
    _chrome_refresh(force=True)


def _chrome_disable() -> None:
    """Restore normal terminal scrolling."""
    global _chrome_enabled, _chrome_prompt, _chrome_input_active, _chrome_partial
    global _chrome_last_reserve, _chrome_last_state
    if _chrome_enabled:
        sys.stdout.write("\033[r\033[?25h")
        sys.stdout.flush()
    _chrome_enabled = False
    _chrome_prompt = ""
    _chrome_input_active = False
    _chrome_partial = ""
    _chrome_last_reserve = 0
    _chrome_last_state = None


def _repl_print(*args, **kwargs):
    """Print into the scroll region without clobbering the pinned prompt."""
    file = kwargs.get("file", sys.stdout)
    if file is not sys.stdout or not _chrome_enabled:
        _builtin_print(*args, **kwargs)
        return

    sep = kwargs.pop("sep", " ")
    end = kwargs.pop("end", "\n")
    kwargs.pop("flush", None)
    kwargs.pop("file", None)

    text = sep.join(str(a) for a in args)
    with _repl_print_lock:
        partial = _chrome_partial if _chrome_input_active else ""

        bottom = _chrome_update_scroll_region(reposition=True)
        sys.stdout.write(f"\033[{bottom};1H\033[K{text}{end}")
        sys.stdout.flush()

        _chrome_draw(partial=partial)


def _repl_print_lines(text: str) -> None:
    """Print multi-line text into the scroll region without clobbering chrome."""
    if not text:
        return
    for line in text.split("\n"):
        _repl_print(line)


def _tty_input_available() -> bool:
    return sys.stdin.isatty() and termios is not None and tty is not None


def _input_history_items() -> list[str]:
    """Submitted REPL lines for up-arrow recall."""
    return list(_session_input_history)


def _remember_input_line(line: str) -> None:
    """Record a submitted line for up-arrow history."""
    if not line:
        return
    global _session_input_history
    if not _session_input_history or _session_input_history[-1] != line:
        _session_input_history.append(line)


def _tty_stdin_raw_fd() -> int:
    """File descriptor for unbuffered stdin bytes (avoids TextIO read-ahead)."""
    if hasattr(sys.stdin, "buffer") and hasattr(sys.stdin.buffer, "raw"):
        return sys.stdin.buffer.raw.fileno()
    return sys.stdin.fileno()


def _tty_read_stdin_bytes(count: int) -> bytes:
    """Read *count* bytes from stdin without the TextIO layer."""
    if _tty_read_byte_hook is not None:
        # test seam: reads go via byte_hook in _read_stdin_byte
        return b""
    if hasattr(sys.stdin, "buffer") and hasattr(sys.stdin.buffer, "raw"):
        return sys.stdin.buffer.raw.read(count) or b""
    return os.read(sys.stdin.fileno(), count)


def _tty_reset_stdin_reader() -> None:
    """Reset stdin reader state at interactive session start."""
    global _tty_stdin_unread
    _tty_stdin_unread = []


def _tty_read_stdin_byte(timeout: float) -> str | None:
    """Read one keyboard byte when select reports ready within *timeout* seconds."""
    global _tty_stdin_unread, _tty_read_byte_hook
    if _tty_stdin_unread:
        return _tty_stdin_unread.pop(0)
    if _tty_read_byte_hook is not None:
        if not _is_test_context():
            _tty_read_byte_hook = None  # prod guard
        else:
            return _tty_read_byte_hook(timeout)
    fd = _tty_stdin_raw_fd()
    if select.select([fd], [], [], timeout)[0]:
        data = _tty_read_stdin_bytes(1)
        if not data:
            return None
        return data.decode("latin-1")
    return None


def _tty_unread_stdin_byte(ch: str) -> None:
    """Push one byte back for the next stdin read (escape parse must not eat \\n)."""
    global _tty_stdin_unread
    _tty_stdin_unread.insert(0, ch)


def _tty_unread_stdin_many(chars: list[str]) -> None:
    """Push bytes back in original order for the next stdin reads."""
    for ch in reversed(chars):
        _tty_unread_stdin_byte(ch)


def _tty_slurp_stdin(stop_on_newline: bool = True) -> list[str]:
    """Read every byte already buffered on stdin (avoids ESC/CSI splits)."""
    if _tty_read_byte_hook is not None:
        # test seam: no real fd; tests feed exact seqs via hook for byte reads
        return []
    fd = _tty_stdin_raw_fd()
    pending: list[str] = []
    while select.select([fd], [], [], 0)[0]:
        chunk = _tty_read_stdin_bytes(1)
        if not chunk:
            break
        for ch in chunk.decode("latin-1"):
            if stop_on_newline and ch in ("\n", "\r"):
                _tty_unread_stdin_byte(ch)
                return pending
            pending.append(ch)
    return pending


def _tty_try_parse_esc_from_pending(pending: list[str]) -> tuple[str | None, int]:
    """If *pending* starts with a complete CSI/SS3 sequence, return (seq, bytes_used)."""
    if not pending:
        return None, 0
    if pending[0] == "O":
        if len(pending) < 2:
            return None, 0
        return "O" + pending[1], 2
    if pending[0] != "[":
        return None, 0
    if len(pending) < 2:
        return None, 0
    if pending[1].isalpha():
        return "[" + pending[1], 2
    if pending[1].isdigit() or pending[1] == ";":
        idx = 1
        while idx < len(pending):
            if pending[idx].isalpha() or pending[idx] == "~":
                return "".join(pending[: idx + 1]), idx + 1
            idx += 1
        return None, 0
    return None, 0


def _tty_arrow_letter(seq: str) -> str | None:
    """Map CSI/SS3 arrow sequences to A/B/C/D (e.g. '[A', 'OA', '[1;2A')."""
    if not seq:
        return None
    if len(seq) == 2 and seq[0] == "O" and seq[1] in "ABCD":
        return seq[1]
    if seq.startswith("[") and len(seq) >= 2 and seq[-1] in "ABCD":
        return seq[-1]
    return None


def _tty_collect_esc_sequence() -> str | None:
    """After ESC was read: None = lone Escape; else CSI/SS3 suffix (e.g. '[A').

    Keeps reading until a full arrow/function sequence is assembled or the
    deadline expires. Prevents orphaned ``[A`` bytes leaking into the line buffer.
    """
    deadline = time.monotonic() + _ESC_SEQUENCE_WAIT_S
    pending = _tty_slurp_stdin()
    while time.monotonic() < deadline:
        if not pending:
            if _tty_stdin_unread and _tty_stdin_unread[0] in ("\n", "\r"):
                return None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ch = _tty_read_stdin_byte(min(remaining, _CSI_POLL_INTERVAL_S))
            if ch is None:
                continue
            if ch in ("\n", "\r"):
                _tty_unread_stdin_byte(ch)
                return None
            pending.append(ch)

        if pending[0] not in ("[", "O"):
            _tty_unread_stdin_many(pending)
            return None

        seq, used = _tty_try_parse_esc_from_pending(pending)
        if seq is not None:
            del pending[:used]
            if pending:
                _tty_unread_stdin_many(pending)
            return seq

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        ch = _tty_read_stdin_byte(min(remaining, _CSI_POLL_INTERVAL_S))
        if ch is None:
            continue
        if ch in ("\n", "\r"):
            _tty_unread_stdin_byte(ch)
            if pending:
                _tty_unread_stdin_many(pending)
            return None
        pending.append(ch)

    if pending:
        _tty_unread_stdin_many(pending)
    return None


def _tty_set_partial(partial: str) -> None:
    global _chrome_partial
    _chrome_partial = partial
    with _repl_print_lock:
        _chrome_refresh(force=True)


def _tty_refresh_input(buf: list[str], pos: int) -> None:
    """Redraw the in-progress input line and place the cursor."""
    text = "".join(buf)
    _tty_set_partial(text)
    tail = len(buf) - pos
    if tail > 0:
        sys.stdout.write(f"\033[{tail}D")
        sys.stdout.flush()


def _tty_apply_arrow_key(
    arrow: str,
    *,
    buf: list[str],
    pos: int,
    history_items: list[str],
    history_index: int | None,
    history_draft: str,
) -> tuple[list[str], int, int | None, str]:
    """Apply up/down/left/right history or cursor movement."""
    if arrow == "A":  # up — previous history entry
        if not history_items:
            return buf, pos, history_index, history_draft
        if history_index is None:
            history_draft = "".join(buf)
            history_index = len(history_items)
        if history_index > 0:
            history_index -= 1
            buf = list(history_items[history_index])
            pos = len(buf)
            _tty_refresh_input(buf, pos)
        return buf, pos, history_index, history_draft
    if arrow == "B":  # down — next history entry
        if history_index is None:
            return buf, pos, history_index, history_draft
        if history_index < len(history_items) - 1:
            history_index += 1
            buf = list(history_items[history_index])
        else:
            history_index = None
            buf = list(history_draft)
        pos = len(buf)
        _tty_refresh_input(buf, pos)
        return buf, pos, history_index, history_draft
    if arrow == "D" and pos > 0:  # left
        pos -= 1
        sys.stdout.write("\033[D")
        sys.stdout.flush()
    elif arrow == "C" and pos < len(buf):  # right
        pos += 1
        sys.stdout.write("\033[C")
        sys.stdout.flush()
    return buf, pos, history_index, history_draft


def _tty_try_read_orphan_csi(prefix: str) -> str | None:
    """Parse CSI/SS3 that lost its leading ESC (TextIO read-ahead edge case)."""
    pending = [prefix] + _tty_slurp_stdin(stop_on_newline=False)
    deadline = time.monotonic() + 0.05
    while time.monotonic() < deadline:
        seq, used = _tty_try_parse_esc_from_pending(pending)
        if seq is not None:
            if used < len(pending):
                _tty_unread_stdin_many(pending[used:])
            return seq
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        ch = _tty_read_stdin_byte(min(remaining, _CSI_POLL_INTERVAL_S))
        if ch is None:
            break
        if ch in ("\n", "\r"):
            _tty_unread_stdin_byte(ch)
            break
        pending.append(ch)
    _tty_unread_stdin_many(pending)
    return None


def _tty_read_line() -> str:
    """Read a line in cbreak mode: arrows, history, and Escape to skip running work."""
    global _tty_stdin_unread
    if _tty_read_byte_hook is None:
        assert termios is not None and tty is not None
    buf: list[str] = []
    pos = 0
    history_items = _input_history_items()
    history_index: int | None = None
    history_draft = ""
    _tty_stdin_unread = []
    use_real_tty = _tty_read_byte_hook is None
    fd = sys.stdin.fileno() if use_real_tty else None
    old = termios.tcgetattr(fd) if use_real_tty else None
    try:
        if use_real_tty:
            tty.setcbreak(fd)
        _tty_set_partial("")
        while True:
            ch = _tty_read_stdin_byte(0.1)
            if ch is None:
                continue
            if ch in ("\n", "\r"):
                line = "".join(buf)
                if _chrome_enabled:
                    # Do not emit a scroll newline on submit — chrome redraws the prompt row.
                    _chrome_update_scroll_region(reposition=True)
                    _chrome_draw(partial="")
                else:
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                return line
            if ch in ("\x7f", "\b"):
                if pos > 0:
                    buf.pop(pos - 1)
                    pos -= 1
                    _tty_refresh_input(buf, pos)
                continue
            if ch == "\x03":
                raise KeyboardInterrupt
            if ch == "\x1b":
                seq = _tty_collect_esc_sequence()
                if seq is None:
                    if _current_command:
                        _request_skip_current()
                    continue
                arrow = _tty_arrow_letter(seq)
                if arrow is not None:
                    buf, pos, history_index, history_draft = _tty_apply_arrow_key(
                        arrow,
                        buf=buf,
                        pos=pos,
                        history_items=history_items,
                        history_index=history_index,
                        history_draft=history_draft,
                    )
                    continue
                if seq in ("[H", "[1~", "OH"):  # home
                    if pos > 0:
                        sys.stdout.write(f"\033[{pos}D")
                        sys.stdout.flush()
                        pos = 0
                    continue
                if seq in ("[F", "[4~", "OF"):  # end
                    tail = len(buf) - pos
                    if tail > 0:
                        sys.stdout.write(f"\033[{tail}C")
                        sys.stdout.flush()
                        pos = len(buf)
                    continue
                continue
            if ch in ("[", "O"):
                seq = _tty_try_read_orphan_csi(ch)
                if seq is not None:
                    arrow = _tty_arrow_letter(seq)
                    if arrow is not None:
                        buf, pos, history_index, history_draft = _tty_apply_arrow_key(
                            arrow,
                            buf=buf,
                            pos=pos,
                            history_items=history_items,
                            history_index=history_index,
                            history_draft=history_draft,
                        )
                        continue
                    if seq in ("[H", "[1~", "OH") and pos > 0:
                        sys.stdout.write(f"\033[{pos}D")
                        sys.stdout.flush()
                        pos = 0
                        continue
                    if seq in ("[F", "[4~", "OF"):
                        tail = len(buf) - pos
                        if tail > 0:
                            sys.stdout.write(f"\033[{tail}C")
                            sys.stdout.flush()
                            pos = len(buf)
                        continue
                    continue
            if ch.isprintable() or ch == "\t":
                buf.insert(pos, ch)
                pos += 1
                history_index = None
                history_draft = ""
                _tty_refresh_input(buf, pos)
    finally:
        if use_real_tty and old is not None:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _patch_repl_print(active: bool) -> None:
    global _repl_print_patched
    if active and not _repl_print_patched:
        builtins.print = _repl_print
        _repl_print_patched = True
    elif not active and _repl_print_patched:
        builtins.print = _builtin_print
        _repl_print_patched = False


async def _prompt_input(prompt: str) -> str:
    """Serialize all stdin reads through a single async lock."""
    global _test_prompt_input_hook, _tty_read_byte_hook
    async with _stdin_lock:

        def _setup_chrome_for_prompt():
            global _chrome_prompt, _chrome_input_active, _chrome_partial
            if _chrome_enabled:
                with _repl_print_lock:
                    _chrome_prompt = prompt
                    _chrome_input_active = True
                    _chrome_partial = ""
                    _chrome_refresh(force=True)

        def _teardown_chrome_after_prompt():
            global _chrome_input_active, _chrome_prompt, _chrome_partial
            if _chrome_enabled:
                with _repl_print_lock:
                    _chrome_input_active = False
                    _chrome_partial = ""
                    # do not blank _chrome_prompt: allows _chrome_draw in post-output
                    # _repl_print calls to restore clean prompt row after commands
                    _chrome_refresh(force=True)

        if _test_prompt_input_hook is not None:
            if not _is_test_context():
                _test_prompt_input_hook = None  # prod guard: seams only for tests
            else:
                _setup_chrome_for_prompt()
                try:
                    # Always to_thread for hook (consistent with _read path; avoids starving loop)
                    raw = await asyncio.to_thread(
                        lambda p=prompt: _test_prompt_input_hook(p)
                    )
                finally:
                    _teardown_chrome_after_prompt()
                _remember_input_line(raw)
                return raw.strip()

        def _read() -> str:
            global _chrome_prompt, _chrome_input_active, _chrome_partial
            if not _chrome_enabled:
                return input(prompt)
            with _repl_print_lock:
                _chrome_prompt = prompt
                _chrome_input_active = True
                _chrome_partial = ""
                _chrome_refresh(force=True)
            try:
                # Do not hold _repl_print_lock during input — worker must stream live.
                if _tty_input_available():
                    return _tty_read_line()
                return input("")
            finally:
                with _repl_print_lock:
                    _chrome_input_active = False
                    _chrome_partial = ""
                    # do not blank _chrome_prompt: allows post-command repl_print draws
                    # to include clean prompt in chrome (0% jank after list/recipe/etc)
                    _chrome_refresh(force=True)

        raw = await asyncio.to_thread(_read)
        _remember_input_line(raw)
        return raw.strip()


def _is_confirm_answer(line: str) -> bool:
    return line.strip().lower() in ("y", "yes", "n", "no", "")


def _deliver_confirm_answer(line: str) -> bool:
    """Route a y/n answer to the active confirmation. Returns True if handled."""
    if not _is_confirm_answer(line):
        return False
    if _confirm_future is None or _confirm_future.done():
        return False
    _confirm_future.set_result(line.strip())
    return True


def _waiting_for_confirm() -> bool:
    return _confirm_future is not None and not _confirm_future.done()


def _route_confirm_input(line: str) -> bool:
    """Deliver y/n to active confirm or buffer until the worker is ready."""
    if not _is_confirm_answer(line):
        return False
    if _deliver_confirm_answer(line):
        return True
    if _bulk_confirm_pending:
        global _confirm_answer_buffer
        _confirm_answer_buffer = line.strip()
        return True
    return False


def _command_may_bulk_confirm(line: str) -> bool:
    return line.split()[0] in _BULK_CONFIRM_COMMANDS


def _awaiting_bulk_confirm_setup() -> bool:
    """True while a bulk command is starting but confirm UI is not ready yet."""
    return (
        _current_command is not None
        and not _bulk_confirm_resolved
        and not _waiting_for_confirm()
    )


async def _await_confirmation(prompt: str) -> str:
    """Request confirmation via the interactive loop (avoids competing craft> prompts)."""
    if not _interactive_mode_active:
        return await _prompt_input(prompt)
    global _confirm_future, _confirm_expected, _confirm_answer_buffer, _chrome_prompt, _chrome_partial
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[str] = loop.create_future()
    _confirm_future = fut
    _confirm_expected = True
    if _chrome_enabled:
        with _repl_print_lock:
            _chrome_prompt = _craft_prompt()
            _chrome_partial = ""
            _chrome_refresh(force=True)
    else:
        _chrome_sync()
    if _confirm_answer_buffer is not None:
        answer = _confirm_answer_buffer
        _confirm_answer_buffer = None
        _confirm_future = None
        return answer
    try:
        return await fut
    finally:
        _confirm_future = None
        if _chrome_enabled:
            with _repl_print_lock:
                _chrome_partial = ""
                # keep _chrome_prompt for post-output draws to restore clean prompt row
                _chrome_refresh(force=True)
        else:
            _chrome_sync()


async def _sleep_cancellable_async(seconds: float, step: float = 0.1) -> bool:
    """Async sleep in small chunks; return True if cancelled during sleep."""
    elapsed = 0.0
    while elapsed < seconds:
        if _cancelled:
            return True
        chunk = min(step, seconds - elapsed)
        await asyncio.sleep(chunk)
        elapsed += chunk
    return _cancelled


def _run_sync(coro):
    """Run a coroutine from synchronous CLI entry points."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
        return
    raise RuntimeError("use the async entry point from interactive mode")


async def _combine_pairs(client, storage, pairs: list[tuple]):
    """Combine a list of (element, element) pairs with light parallelism."""
    total = len(pairs)
    new_count = 0
    nothing_count = 0
    done_count = 0
    known_names = {e.name for e in storage.get_all()}

    async def process(a, b):
        nonlocal new_count, nothing_count, done_count
        try:
            result = await _cached_pair(client, storage, a, b)
        except CommandCancelled:
            return
        except Exception as e:
            done_count += 1
            _repl_print_lines(
                f"  [{done_count}/{total}] {format_element(a)} + {format_element(b)} = "
                f"{_color(f'Error: {_tty(str(e))}', RED)}"
            )
            return
        done_count += 1
        if result.name is not None:
            for elem in (a, b):
                storage.add(
                    name=_sanitize_element_name(elem.name),
                    emoji=elem.emoji,
                    is_first_discovery=False,
                )
            storage.add(
                name=_sanitize_element_name(result.name),
                emoji=result.emoji,
                is_first_discovery=result.is_first_discovery,
            )
        result_display = result.name if result.name else "Nothing"
        _history.append((a.name, b.name, result_display))
        if result.name is None:
            nothing_count += 1
        else:
            tag = ""
            if result.name not in known_names:
                tag = " " + _color("[NEW]", BOLD + GREEN)
                new_count += 1
                known_names.add(result.name)
            _repl_print_lines(
                f"  [{done_count}/{total}] {format_element(a)} + {format_element(b)} = "
                f"{format_element(result)}{tag}"
            )

    # Process in batches of API_CONCURRENCY to avoid overwhelming the rate limiter
    for i in range(0, len(pairs), API_CONCURRENCY):
        if _cancelled:
            break
        batch = pairs[i : i + API_CONCURRENCY]
        await asyncio.gather(*(process(a, b) for a, b in batch))

    if _cancelled:
        _repl_print_lines(
            f"  Cancelled. {_color(str(new_count), GREEN)} new, "
            f"{nothing_count} nothing, {done_count}/{total} tried."
        )
        _mark_cancel_notified()
    else:
        _repl_print_lines(
            f"  Done. {_color(str(new_count), GREEN)} new, {nothing_count} nothing, {total} tried."
        )


async def _confirm_and_run_pairs(client, storage, pairs: list[tuple]):
    """Warn if too many pairs, then run them."""
    global _confirm_expected, _bulk_confirm_pending, _bulk_confirm_resolved
    if len(pairs) <= _BULK_WARN_THRESHOLD:
        _bulk_confirm_resolved = True
    if len(pairs) > _BULK_WARN_THRESHOLD:
        _repl_print_lines(
            f"  {_color(f'{len(pairs)} pairs', YELLOW)} — "
            f"type {_color('y', BOLD)} or {_color('yes', BOLD)} to continue"
        )
        if sys.stdin.isatty():
            _bulk_confirm_pending = True
            _chrome_sync()
            try:
                try:
                    answer = (await _await_confirmation("")).strip().lower()
                except (EOFError, KeyboardInterrupt):
                    _repl_print_lines("  Cancelled.")
                    _mark_cancel_notified()
                    return
                if answer not in ("y", "yes"):
                    _repl_print_lines("  Cancelled.")
                    _mark_cancel_notified()
                    return
            finally:
                _bulk_confirm_pending = False
                _confirm_expected = False
                _confirm_answer_buffer = None
                _chrome_sync()
        _bulk_confirm_resolved = True
    await _combine_pairs(client, storage, pairs)


async def do_permute(client, storage, query: str):
    """Combine every pair of elements matching the query with each other."""
    matches, err = _match_elements(storage, query)
    if err:
        _repl_print_lines(f"  {err}")
        return
    if not matches:
        _repl_print_lines("  No elements match that query.")
        return
    if len(matches) == 1:
        _repl_print_lines(f"  Only one match: {format_element(matches[0])}. Need at least two.")
        return

    n = len(matches)
    pairs = [(matches[i], matches[j]) for i in range(n) for j in range(i + 1, n)]
    _repl_print_lines(f"  {n} elements match, {len(pairs)} unique pairs:")
    for m in matches:
        _repl_print_lines(f"    {format_element(m)}")
    await _confirm_and_run_pairs(client, storage, pairs)


async def do_permutate(client, storage, query: str):
    """Repeatedly permute matching elements until no new discoveries."""
    global _confirm_expected, _bulk_confirm_resolved
    round_num = 0
    confirmed = False
    stopped = False
    try:
        _repl_print_lines(
            f"  Permutating matches for {_color(query, YELLOW)} until no new discoveries..."
        )
        _repl_print_lines("  (Ctrl+C to stop)")

        while True:
            if _cancelled:
                stopped = True
                break
            if round_num >= _MAX_PERMUTATE_ROUNDS:
                _repl_print_lines(f"  Reached max rounds ({_MAX_PERMUTATE_ROUNDS}). Stopping.")
                break
            round_num += 1
            known_before = {e.name for e in storage.get_all()}
            matches, err = _match_elements(storage, query)
            if err:
                _repl_print_lines(f"  {err}")
                return
            if not matches:
                _repl_print_lines("  No elements match that query.")
                return
            if len(matches) == 1:
                _repl_print_lines(
                    f"  Only one match: {format_element(matches[0])}. Need at least two."
                )
                return

            n = len(matches)
            pairs = [
                (matches[i], matches[j]) for i in range(n) for j in range(i + 1, n)
            ]
            _repl_print_lines(f"  --- Round {round_num}: {n} elements, {len(pairs)} pairs ---")

            if not confirmed and len(pairs) > _BULK_WARN_THRESHOLD:
                _repl_print_lines(
                    f"  {_color(f'{len(pairs)} pairs per round', YELLOW)} — "
                    f"type {_color('y', BOLD)} or {_color('yes', BOLD)} to continue"
                )
                if sys.stdin.isatty():
                    global _bulk_confirm_pending
                    _bulk_confirm_pending = True
                    _chrome_sync()
                    try:
                        try:
                            answer = (await _await_confirmation("")).strip().lower()
                        except (EOFError, KeyboardInterrupt):
                            _repl_print_lines("  Cancelled.")
                            _mark_cancel_notified()
                            return
                        if answer not in ("y", "yes"):
                            _repl_print_lines("  Cancelled.")
                            _mark_cancel_notified()
                            return
                    finally:
                        _bulk_confirm_pending = False
                        _confirm_expected = False
                        _confirm_answer_buffer = None
                        _chrome_sync()
                confirmed = True
                _bulk_confirm_resolved = True
            elif not confirmed:
                _bulk_confirm_resolved = True

            await _combine_pairs(client, storage, pairs)
            known_after = {e.name for e in storage.get_all()}
            new_count = len(known_after - known_before)
            _repl_print_lines(f"  +{new_count} new elements")

            if _cancelled:
                stopped = True
                break
            if new_count == 0:
                _repl_print_lines("  No new discoveries. Stopping.")
                break

        if stopped:
            if not _skip_summary_shown:
                _repl_print_lines("  Stopped.")
                _mark_cancel_notified()
        else:
            _repl_print_lines(f"  Permutate done after {round_num} round(s).")
    finally:
        _confirm_expected = False
        _confirm_answer_buffer = None


async def do_cross(client, storage, left_query: str, right_query: str):
    """Cross-combine all elements matching left_query with all matching right_query."""
    left, left_err = _match_elements(storage, left_query)
    if left_err:
        _repl_print_lines(f"  {left_err}")
        return
    right, right_err = _match_elements(storage, right_query)
    if right_err:
        _repl_print_lines(f"  {right_err}")
        return
    if not left:
        _repl_print_lines(f"  No elements match: {left_query}")
        return
    if not right:
        _repl_print_lines(f"  No elements match: {right_query}")
        return

    # Build pairs, skipping duplicates (a+b == b+a)
    seen = set()
    pairs = []
    for a in left:
        for b in right:
            if a.name == b.name:
                continue
            key = tuple(sorted([a.name, b.name]))
            if key not in seen:
                seen.add(key)
                pairs.append((a, b))

    if not pairs:
        _repl_print_lines("  No valid pairs (all matches overlap).")
        return

    _repl_print_lines(
        f"  Left ({len(left)}): "
        f"{', '.join(format_element(e) for e in left[:10])}{'...' if len(left) > 10 else ''}"
    )
    _repl_print_lines(
        f"  Right ({len(right)}): "
        f"{', '.join(format_element(e) for e in right[:10])}{'...' if len(right) > 10 else ''}"
    )
    _repl_print_lines(f"  {len(pairs)} unique pairs")
    await _confirm_and_run_pairs(client, storage, pairs)


# ---------------------------------------------------------------------------
# Infinibrowser integration
# ---------------------------------------------------------------------------
_IB_BASE = "https://infinibrowser.wiki/api"


def _ib_fetch(path: str, params: dict, use_cache: bool = True) -> dict | None:
    """Fetch from the Infinibrowser API. Prints errors on failure."""
    result = fetch_json(f"{_IB_BASE}/{path}", params=params, use_cache=use_cache)
    if result is None:
        _repl_print_lines(f"  {_color('Infinibrowser request failed', RED)}")
    return result


def _ib_fetch_quiet(path: str, params: dict) -> dict | None:
    """Fetch from the Infinibrowser API. Silent on errors."""
    return fetch_json(f"{_IB_BASE}/{path}", params=params)


async def _ib_fetch_async(
    path: str, params: dict, *, use_cache: bool = True
) -> dict | None:
    return await asyncio.to_thread(_ib_fetch, path, params, use_cache)


async def _ib_fetch_quiet_async(path: str, params: dict) -> dict | None:
    return await asyncio.to_thread(_ib_fetch_quiet, path, params)


async def _ib_can_fill_async(name: str) -> bool | None:
    return await asyncio.to_thread(_ib_can_fill, name)


async def _import_from_infinibrowser_async(storage, name: str) -> str:
    """Look up an element on Infinibrowser, show its lineage, and import into discoveries."""
    data = await _ib_fetch_async("item", {"id": name})
    if data is None:
        return ""
    if "code" in data:
        return f"  {_color('Not found', DIM)} on Infinibrowser: {format_element(Element(name=name))}"

    emoji = data.get("emoji", "")
    depth = data.get("depth", "?")
    item_name = _sanitize_element_name(data["text"])
    found_elem = Element(name=item_name, emoji=emoji or None, is_first_discovery=None)
    _repl_print_lines(f"  Found: {format_element(found_elem)}  (depth {depth})")

    lineage = await _ib_fetch_async("recipe", {"id": name}, use_cache=False)
    if lineage is None:
        return ""
    steps = lineage.get("steps", [])
    if not steps:
        return f"  No lineage available for {format_element(Element(name=name))}."

    _repl_print_lines(f"  Lineage ({len(steps)} steps):")
    imported = set()
    recipe_batch: list[tuple[str, str, str]] = []
    import_batch: list[tuple[str, str | None, bool | None]] = []
    for step in steps:
        a_name = _sanitize_element_name(step["a"]["id"])
        a_emoji = step["a"]["emoji"]
        b_name = _sanitize_element_name(step["b"]["id"])
        b_emoji = step["b"]["emoji"]
        r_name = _sanitize_element_name(step["result"]["id"])
        r_emoji = step["result"]["emoji"]
        a_elem = Element(name=a_name, emoji=a_emoji or None, is_first_discovery=None)
        b_elem = Element(name=b_name, emoji=b_emoji or None, is_first_discovery=None)
        r_elem = Element(name=r_name, emoji=r_emoji or None, is_first_discovery=None)
        _repl_print_lines(
            f"    {format_element(a_elem)} + {format_element(b_elem)} = {format_element(r_elem)}"
        )
        recipe_batch.append((r_name, a_name, b_name))
        for elem_name, elem_emoji in [
            (a_name, a_emoji),
            (b_name, b_emoji),
            (r_name, r_emoji),
        ]:
            if elem_name not in imported:
                import_batch.append((elem_name, elem_emoji, False))
                imported.add(elem_name)
    _record_recipes_batch(recipe_batch)
    storage.add_batch(import_batch)

    storage.reload()
    return f"  Imported {_color(str(len(imported)), GREEN)} elements into discoveries."


def _import_from_infinibrowser(storage, name: str) -> str:
    return _run_sync(_import_from_infinibrowser_async(storage, name))


def _import_from_save(storage, path: str) -> str:
    """Import elements and recipes from an .ic save file into discoveries."""
    try:
        compressed_size = os.path.getsize(path)
        if compressed_size > _MAX_IC_COMPRESSED_BYTES:
            return (
                f"  {_color('Save file too large', RED)} "
                f"(max {_MAX_IC_COMPRESSED_BYTES // (1024 * 1024)} MiB compressed)"
            )
        with gzip.open(path, "rb") as f:
            raw = f.read(_MAX_IC_DECOMPRESSED_BYTES + 1)
        if len(raw) > _MAX_IC_DECOMPRESSED_BYTES:
            return (
                f"  {_color('Decompressed save too large', RED)} "
                f"(max {_MAX_IC_DECOMPRESSED_BYTES // (1024 * 1024)} MiB)"
            )
        save = json.loads(raw.decode("utf-8"))
    except Exception as e:
        return f"  {_color(f'Error reading save file: {e}', RED)}"

    items = save.get("items", [])
    if not items:
        return "  No items in save file."
    if len(items) > _MAX_IC_ITEMS:
        return f"  {_color('Too many items in save file', RED)} (max {_MAX_IC_ITEMS:,})"

    # Build id-to-name lookup
    id_to_item = {item["id"]: item for item in items}

    batch: list[tuple[str, str | None, bool | None]] = []
    for item in items:
        batch.append(
            (
                _sanitize_element_name(item["text"]),
                item.get("emoji", ""),
                item.get("discovery", False),
            )
        )
    imported_count = storage.add_batch(batch)

    recipe_batch: list[tuple[str, str, str]] = []
    for item in items:
        name = _sanitize_element_name(item["text"])
        for recipe in item.get("recipes", []):
            if len(recipe) == 2 and recipe[0] in id_to_item and recipe[1] in id_to_item:
                a_name = _sanitize_element_name(id_to_item[recipe[0]]["text"])
                b_name = _sanitize_element_name(id_to_item[recipe[1]]["text"])
                recipe_batch.append((name, a_name, b_name))
    _record_recipes_batch(recipe_batch)
    recipe_count = len(recipe_batch)

    storage.reload()
    total = len(items)
    return (
        f"  Loaded {_color(str(total), GREEN)} elements "
        f"({imported_count} new) with {recipe_count} recipes from {_color(path, BOLD)}"
    )


async def do_import_async(storage, arg: str) -> str:
    """Import from Infinibrowser (element name) or .ic save file (path)."""
    if arg.endswith(".ic") or os.path.sep in arg:
        return await asyncio.to_thread(_import_from_save, storage, arg)
    return await _import_from_infinibrowser_async(storage, arg)


def do_import(storage, arg: str) -> str:
    return _run_sync(do_import_async(storage, arg))


_BASE_ELEMENTS = {"Water", "Fire", "Wind", "Earth"}


async def _fill_missing_recipes_async(storage):
    """Fetch lineages from Infinibrowser for elements missing recipes.

    When a lineage is fetched, its intermediate elements get recipes too,
    so we re-check the missing set after each fetch to skip already-filled items.
    """
    recipes = _load_recipes()
    name_set = {e.name for e in storage.get_all()}
    missing = {
        e.name
        for e in storage.get_all()
        if e.name not in _BASE_ELEMENTS and e.name not in recipes
    }
    if not missing:
        _repl_print_lines("  All elements have recipes.")
        return

    total = len(missing)
    _repl_print_lines(f"  {total} elements missing recipes. Fetching from Infinibrowser...")
    _repl_print_lines("  (Ctrl+C to stop early)")
    fetched = 0
    skipped = 0
    failed = set()
    processed = 0
    queue = sorted(missing)
    try:
        for name in queue:
            if _cancelled:
                _repl_print_lines("  Stopped early.")
                _mark_cancel_notified()
                break
            recipes = _load_recipes()
            if name in recipes or name in failed:
                skipped += 1
                continue
            processed += 1
            remaining = total - fetched - skipped - len(failed)
            e = storage.get_by_name(name) or Element(name=name)
            _repl_print_lines(
                f"  [{processed}/{total}] {format_element(e)} ({remaining} remaining)..."
            )
            data = await _ib_fetch_quiet_async("item", {"id": name})
            if data is None or "code" in data:
                failed.add(name)
                continue
            lineage = await _ib_fetch_quiet_async("recipe", {"id": name})
            if lineage is None:
                failed.add(name)
                continue
            for step in lineage.get("steps", []):
                a_name, a_emoji = step["a"]["id"], step["a"]["emoji"]
                b_name, b_emoji = step["b"]["id"], step["b"]["emoji"]
                r_name, r_emoji = step["result"]["id"], step["result"]["emoji"]
                _record_recipe(r_name, a_name, b_name)
                for elem_name, elem_emoji in [
                    (a_name, a_emoji),
                    (b_name, b_emoji),
                    (r_name, r_emoji),
                ]:
                    if elem_name not in name_set:
                        storage.add(
                            name=_sanitize_element_name(elem_name),
                            emoji=elem_emoji,
                            is_first_discovery=False,
                        )
                        name_set.add(elem_name)
            fetched += 1
            if await _sleep_cancellable_async(0.5):
                _repl_print_lines("  Stopped early.")
                _mark_cancel_notified()
                break
    except KeyboardInterrupt:
        _repl_print_lines("  Stopped early.")
    _mark_cancel_notified()
    storage.reload()
    summary = f"  Fetched {fetched} lineages, {skipped} already filled by prior lineages."
    if failed:
        summary += f" {_color(str(len(failed)), YELLOW)} not found on Infinibrowser."
    _repl_print_lines(summary)


def _fill_missing_recipes(storage):
    _run_sync(_fill_missing_recipes_async(storage))


def do_unfilled(storage) -> str:
    """List elements that have no recipes (excluding base elements)."""
    recipes = _load_recipes()
    discoveries = storage.get_all()
    missing = [
        e for e in discoveries if e.name not in _BASE_ELEMENTS and e.name not in recipes
    ]
    if not missing:
        return "  All elements have recipes."
    lines = [f"  {len(missing)} elements without recipes:"]
    for e in missing:
        lines.append(f"    {format_element(e)}")
    return "\n".join(lines)


def _included_element_names(
    recipes: dict[str, list[list[str]]] | None = None,
) -> set[str]:
    """Names in the export/prune closure: bases, recipe results, and their constituents."""
    if recipes is None:
        recipes = _load_recipes()
    included = set(_BASE_ELEMENTS)
    for name, pairs in recipes.items():
        if pairs:
            included.add(name)
    changed = True
    while changed:
        changed = False
        for name in list(included):
            if name not in recipes:
                continue
            for a, b in recipes[name]:
                if a not in included:
                    included.add(a)
                    changed = True
                if b not in included:
                    included.add(b)
                    changed = True
    return included


def _orphan_candidates(storage) -> list:
    """Discoveries with no recipe lineage and not referenced as a constituent."""
    included = _included_element_names()
    return [e for e in storage.get_all() if e.name not in included]


def _ib_can_fill(name: str) -> bool | None:
    """Whether /fill could fetch a recipe for name.

    Returns True if fillable, False if Infinibrowser has no recipe, None on API error.
    """
    try:
        item_resp = _get_sync_session().get(
            f"{_IB_BASE}/item", params={"id": name}, timeout=15
        )
        if item_resp.status_code == 404:
            return False
        if not item_resp.ok:
            return None
        item_data = item_resp.json()
        if "code" in item_data:
            return False

        recipe_resp = _get_sync_session().get(
            f"{_IB_BASE}/recipe", params={"id": name}, timeout=15
        )
        if recipe_resp.status_code == 404:
            return False
        if not recipe_resp.ok:
            return None
        recipe_data = recipe_resp.json()
        if "code" in recipe_data:
            return False
        steps = recipe_data.get("steps", recipe_data.get("recipe", []))
        return bool(steps)
    except Exception:
        return None


async def _prune_orphans_async(storage):
    """Remove orphan discoveries that Infinibrowser confirms have no recipe."""
    candidates = _orphan_candidates(storage)
    if not candidates:
        _repl_print_lines("  Nothing to prune.")
        return

    total = len(candidates)
    _repl_print_lines(
        f"  {total} orphan element{'s' if total != 1 else ''} to check on Infinibrowser..."
    )
    _repl_print_lines("  (Ctrl+C to stop early)")
    pruned = 0
    skipped = 0
    kept = 0
    try:
        for i, elem in enumerate(candidates, 1):
            if _cancelled:
                _repl_print_lines("  Stopped early.")
                _mark_cancel_notified()
                break
            _repl_print_lines(
                f"  [{i}/{total}] {format_element(elem)}..."
            )
            fillable = await _ib_can_fill_async(elem.name)
            if fillable is None:
                skipped += 1
            elif fillable:
                kept += 1
            else:
                storage.remove(elem.name)
                pruned += 1
            if await _sleep_cancellable_async(0.5):
                _repl_print_lines("  Stopped early.")
                _mark_cancel_notified()
                break
    except KeyboardInterrupt:
        _repl_print_lines("  Stopped early.")
    _mark_cancel_notified()
    summary = f"  Pruned {_color(str(pruned), GREEN)} element{'s' if pruned != 1 else ''}."
    if kept:
        summary += f" {kept} fillable on Infinibrowser (kept)."
    if skipped:
        summary += f" {_color(str(skipped), YELLOW)} skipped (API errors)."
    _repl_print_lines(summary)


def _prune_orphans(storage):
    _run_sync(_prune_orphans_async(storage))


def do_export(storage, path: str = EXPORT_PATH) -> str:
    """Export discoveries to an Infinite Craft .ic save file.

    Includes elements that have recipes, are base elements, or are referenced
    as constituents by any included recipe (e.g. terminal leaves from /fill
    or /import lineages). This ensures filled recipes survive export/import.
    Pure orphans with no recipes and not referenced by any recipe are excluded.
    """
    recipes = _load_recipes()
    discoveries = storage.get_all()
    included = _included_element_names(recipes)

    # Build export items for the closure
    name_to_id = {}
    items = []
    idx = 0
    for elem in discoveries:
        if elem.name not in included:
            continue
        name_to_id[elem.name] = idx
        item = {"id": idx, "text": elem.name, "emoji": elem.emoji or ""}
        if elem.is_first_discovery:
            item["discovery"] = True
        items.append(item)
        idx += 1

    # Attach recipes using local IDs
    for item in items:
        name = item["text"]
        if name in recipes:
            item_recipes = []
            for pair in recipes[name]:
                a, b = pair[0], pair[1]
                if a in name_to_id and b in name_to_id:
                    item_recipes.append([name_to_id[a], name_to_id[b]])
            if item_recipes:
                item["recipes"] = item_recipes

    now = int(time.time() * 1000)
    save = {
        "name": "CLI Export",
        "version": "1.0",
        "created": now,
        "updated": now,
        "instances": [],
        "items": items,
    }

    excluded = len(discoveries) - len(items)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(save, f)

    msg = (
        f"  Exported {_color(str(len(items)), GREEN)} elements to {_color(path, BOLD)}"
    )
    if excluded:
        msg += f"\n  {_color(str(excluded), YELLOW)} elements excluded (no recipes and not referenced by any included recipe — use /fill to fetch them)"
    return msg


def do_help() -> str:
    return """  Combine:
    <element> + <element>       Combine two elements
    /combine <element> <element>  Combine two elements

  Crawl:
    <element> ++ <element>      Combine & crawl until no new discoveries
    /crawl <element> <element>  Combine & crawl until no new discoveries

  Bulk combine (query syntax below):
    <element> +| <query>        Combine element with all matching discoveries
    /with <element> <query>     Combine element with all matching discoveries
    <query> * <query>           Cross-combine matches from both queries
    /cross <query> <query>    Cross-combine matches from both queries
    /permute <query>            Combine all matching elements with each other
    /permutate <query>          Permute repeatedly until no new discoveries
    /exhaust <query>            Each match combined with all discoveries

  Query syntax (/search, /with, /permute, /permutate, /cross, /exhaust, shorthands):
    substring                   Default: case-insensitive substring
    * ? []                      fnmatch wildcards (e.g. fire*, mu?)
    /pattern/                   Regex, case-insensitive (no | alternation)
    !<query>                    Exclude matches (e.g. !fire* = everything except fire*)
    !                           All elements (exclude nothing)
    ^<query>                    First discoveries only (e.g. ^fire* = new fire* matches)
    ^                           All first discoveries

  Discoveries & recipes:
    /search <query>             Search discoveries
    /recipe <element>           Show shortest recipe from base elements
    /list                       List all discovered elements
    /import <element|file.ic>   Import from Infinibrowser or .ic save file
    /fill                       Fetch missing recipes from Infinibrowser
    /unfilled                   List elements without recipes
    /prune                      Remove orphan elements Infinibrowser can't fill
    /export [path]              Export discoveries as .ic save file
    /history                    Show combinations tried this session
    /queue                      Show running and pending commands (status also appears above craft>)
    /help                       Show this help
    /quit                       Exit

  Background queue (long API commands):
    Esc                         Skip current command, continue to next in queue
                                (TTY only; skips during rate-limit/backoff waits,
                                not during an active network request; bulk
                                commands may finish in-flight pairs first)
    Ctrl+C                      While running: stop and discard remaining queue
                                At bulk confirm [y/N]: decline only (queue kept)"""


# ---------------------------------------------------------------------------
# Interactive REPL
# ---------------------------------------------------------------------------
_API_SLASH_COMMANDS = (
    "/permute",
    "/permutate",
    "/import",
    "/fill",
    "/prune",
    "/export",
    "/exhaust",
    "/combine",
    "/crawl",
    "/with",
    "/cross",
)


def _is_local_command(line: str) -> bool:
    """Commands that run immediately without queuing behind API work."""
    if line in ("/help", "/list", "/history", "/clear", "/queue"):
        return True
    if line == "/unfilled" or line.startswith("/unfilled "):
        return True
    if line == "/search" or line.startswith("/search "):
        return True
    if line == "/recipe" or line.startswith("/recipe "):
        return True
    return False


def _is_slash_command_attempt(line: str) -> bool:
    """True when input looks like a mistyped /command, not a regex cross query."""
    if not line.startswith("/"):
        return False
    if re.match(r"/[^/]+/", line):
        return False
    return bool(re.match(r"/\w", line))


def _classify_command_line(line: str) -> tuple[str, str] | None:
    """Classify a queuable command. Returns (kind, payload) or None if unrecognized."""
    line = line.strip()
    if not line:
        return None

    for cmd in _API_SLASH_COMMANDS:
        rest = _slash_args(line, cmd)
        if rest is not None:
            return cmd.lstrip("/"), rest

    if _is_slash_command_attempt(line):
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


def _validate_query_at_enqueue(query: str) -> str | None:
    """Return an error message if a with/cross query is invalid before enqueue."""
    if len(query) > MAX_QUERY_LENGTH:
        return f"  Query too long (max {MAX_QUERY_LENGTH} characters)"
    q, _, _ = _parse_query_filter(query)
    if not _is_delimited_regex(q):
        return None
    body = q[1:-1]
    if not _regex_is_safe(body):
        return f"  {REGEX_ERROR_COMPLEX}"
    try:
        _regex_module.search(body, "", _regex_module.IGNORECASE, timeout=REGEX_TIMEOUT)
    except TimeoutError:
        return f"  {REGEX_ERROR_COMPLEX}"
    except _regex_module.error:
        return f"  {REGEX_ERROR_INVALID}"
    return None


def _validate_command_line(line: str) -> str | None:
    """Parse and validate a command before enqueue. Returns error text or None if OK."""
    classified = _classify_command_line(line)
    if classified is None:
        if _is_slash_command_attempt(line):
            cmd = line.strip().split()[0]
            return (
                f"  Unknown command: {_color(cmd, YELLOW)}. "
                f"Type {_color('/help', YELLOW)} for commands."
            )
        return f"  Unknown input. Type {_color('/help', YELLOW)} for commands."

    kind, payload = classified

    if kind == "bad+|":
        return (
            "  Use <element> +| <query> "
            f"(no space between + and |). Type {_color('/help', YELLOW)} for commands."
        )

    if kind in ("permute", "permutate", "exhaust", "import"):
        if not payload.strip():
            return (
                f"  Usage: /{kind} <query>"
                if kind != "import"
                else "  Usage: /import <element>"
            )
        if kind == "import":
            return None
        return _validate_query_at_enqueue(payload.strip())

    if kind == "export":
        return None

    if kind in ("fill", "prune"):
        return None

    if kind in ("combine", "crawl"):
        if (pipe_err := _slash_combine_crawl_pipe_error(payload)) is not None:
            return pipe_err
        if (op_err := _slash_combine_crawl_operator_error(payload, kind)) is not None:
            return op_err
        if _parse_two_elements(payload) is None:
            return f"  Usage: /{kind} <element> <element>"
        return None

    if kind == "with":
        parsed = _parse_with_args(payload)
        if parsed is None:
            return "  Usage: /with <element> <query>"
        _, query = parsed
        return _validate_query_at_enqueue(query)

    if kind == "cross":
        if (op_err := _slash_cross_operator_error(payload)) is not None:
            return op_err
        parsed = _parse_cross_queries(payload)
        if parsed is None:
            return "  Usage: /cross <query> <query>"
        left_q, right_q = parsed
        err = _validate_query_at_enqueue(left_q)
        if err:
            return err
        return _validate_query_at_enqueue(right_q)

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
        left_q, right_q = parts[0].strip(), parts[1].strip()
        err = _validate_query_at_enqueue(left_q)
        if err:
            return err
        return _validate_query_at_enqueue(right_q)

    if kind == "+":
        if " + " in payload:
            parts = payload.split(" + ", 1)
        else:
            parts = [payload.rsplit(" +", 1)[0], ""]
        if not parts[0].strip() or not parts[1].strip():
            return "  Usage: <element> + <element>"
        return None

    return None


def _is_recognized_command(line: str) -> bool:
    """Whether interactive input is a known command or shorthand."""
    if _is_local_command(line):
        return True
    if line in ("/quit", "/exit"):
        return True
    return _validate_command_line(line) is None


def do_queue_status() -> str:
    """Describe the current command queue."""
    if not _current_command and not _command_queue:
        if _chrome_enabled:
            status_hint = "its status appears in the panel above craft>."
        else:
            status_hint = "its status appears in the queue panel above the prompt."
        return (
            "  Queue is idle.\n"
            "  When you start a long command (combine, fill, permutate, ...), "
            f"{status_hint}"
        )
    lines: list[str] = []
    if _current_command:
        lines.append(f"  Running: {_sanitize_queue_line(_current_command)}")
    for i, cmd in enumerate(_command_queue, 1):
        lines.append(f"  {i}. pending: {_sanitize_queue_line(cmd)}")
    return "\n".join(lines)


def _format_queue_display() -> str:
    """Render the queue status panel (empty string when idle).

    For exactly one content line (single running / only pending / only awaiting/bulk-confirm),
    emit *only* that status line (no header rule "── queue ──", no footer rule). This reduces
    reserved vertical space by ~2 lines in common case. >1 items keep header+items+footer.
    All truncation/color/sanitize/width preserved exactly.
    """
    running = _current_command
    pending = list(_command_queue)
    awaiting_confirm = _waiting_for_confirm() or _bulk_confirm_pending
    if not running and not pending and not awaiting_confirm:
        return ""
    width = _tty_width()
    content: list[str] = []
    if running:
        cmd = _sanitize_queue_line(running)
        prefix = f"  {_color('▶', YELLOW)} {_color('running', DIM)}  "
        pvis = _ansi_visible_len(prefix)
        avail = max(1, width - pvis - 1)
        if _ansi_visible_len(cmd) > avail:
            cmd = cmd[: max(0, avail - 1)] + "…"
        content.append(f"{prefix}{_color(cmd, YELLOW)}")
    for i, cmd in enumerate(pending, 1):
        safe = _sanitize_queue_line(cmd)
        prefix = f"  {_color(f'{i}.', DIM)} {_color('pending', DIM)}  "
        pvis = _ansi_visible_len(prefix)
        avail = max(1, width - pvis - 1)
        if _ansi_visible_len(safe) > avail:
            safe = safe[: max(0, avail - 1)] + "…"
        content.append(f"{prefix}{safe}")
    if _waiting_for_confirm():
        prefix = f"  {_color('◆', YELLOW)} {_color('awaiting confirm', BOLD + YELLOW)}  "
        plain = "answer y/n at prompt below"
        pvis = _ansi_visible_len(prefix)
        avail = max(1, width - pvis - 1)
        if _ansi_visible_len(plain) > avail:
            plain = plain[: max(0, avail - 1)] + "…"
        desc = _color(plain, DIM)
        content.append(f"{prefix}{desc}")
    elif _bulk_confirm_pending:
        prefix = f"  {_color('◆', YELLOW)} {_color('confirm', BOLD + YELLOW)}  "
        plain = "preparing bulk prompt..."
        pvis = _ansi_visible_len(prefix)
        avail = max(1, width - pvis - 1)
        if _ansi_visible_len(plain) > avail:
            plain = plain[: max(0, avail - 1)] + "…"
        desc = _color(plain, DIM)
        content.append(f"{prefix}{desc}")
    if not content:
        return ""
    if len(content) == 1:
        return content[0]
    overhead = 2 + 1 + 5 + 1
    sep_len = max(3, (width - overhead) // 2)
    sep_len = min(sep_len, 40)
    rule = _color("─" * sep_len, DIM)
    lines: list[str] = [f"  {rule} {_color('queue', BOLD + CYAN)} {rule}"]
    if _ansi_visible_len(lines[0]) > width:
        sep_len = max(1, sep_len - 2)
        rule = _color("─" * sep_len, DIM)
        lines[0] = f"  {rule} {_color('queue', BOLD + CYAN)} {rule}"
    lines.extend(content)
    foot_len = max(3, min(50, width - 2))
    foot = f"  {_color('─' * foot_len, DIM)}"
    if _ansi_visible_len(foot) > width:
        foot_len = max(3, width - 2)
        foot = f"  {_color('─' * foot_len, DIM)}"
    lines.append(foot)
    return "\n".join(lines)


def _erase_queue_panel():
    """Remove the last-painted queue panel from the terminal (TTY only)."""
    global _queue_panel_height
    if _queue_panel_height > 0 and sys.stdout.isatty():
        for _ in range(_queue_panel_height):
            sys.stdout.write("\033[A\033[K")
        sys.stdout.flush()
    _queue_panel_height = 0


def _paint_queue_panel(force: bool = False):
    """Redraw the queue panel above the prompt; clear it when idle."""
    global _last_queue_snapshot, _queue_panel_height
    if _chrome_enabled:
        with _repl_print_lock:
            _chrome_refresh(force=force)
        return
    display = _format_queue_display()
    if display == _last_queue_snapshot and not force:
        return
    _erase_queue_panel()
    if display:
        print(display, flush=True)
        _queue_panel_height = display.count("\n") + 1  # auto 1 for compact single; 3+ for multi
    _last_queue_snapshot = display


def _craft_prompt() -> str:
    """Prompt string; hints when background work is active."""
    if _waiting_for_confirm():
        return _color("confirm [y/N]> ", YELLOW)
    base = _color("craft> ", CYAN)
    if not (_current_command or _command_queue):
        return base
    pending = len(_command_queue) + (1 if _current_command else 0)
    hint = _color(f"[{pending} active] ", DIM)
    if _current_command and _tty_input_available():
        hint += _color("[Esc skip] ", DIM)
    return base + hint


def _queue_enqueue_deferred() -> bool:
    """True when a new command will wait behind in-flight or queued work."""
    return (
        _current_command is not None
        or bool(_command_queue)
        or _waiting_for_confirm()
        or _bulk_confirm_pending
    )


async def _dispatch_line(client, storage, line: str) -> None:
    """Execute one input line from the API worker or immediate local commands."""
    if line == "/help":
        _repl_print_lines(do_help())
    elif (rest := _slash_args(line, "/search")) is not None:
        if not rest:
            msg = "  Usage: /search <query>"
        else:
            msg = do_search(storage, rest)
        _repl_print_lines(msg)
    elif (rest := _slash_args(line, "/recipe")) is not None:
        if not rest:
            msg = "  Usage: /recipe <element>"
        else:
            msg = do_recipe(storage, rest)
        _repl_print_lines(msg)
    elif line == "/list":
        _repl_print_lines(do_list(storage))
    elif (rest := _slash_args(line, "/permute")) is not None:
        if not rest:
            msg = "  Usage: /permute <query>"
            _repl_print_lines(msg)
        else:
            await do_permute(client, storage, rest)
    elif (rest := _slash_args(line, "/permutate")) is not None:
        if not rest:
            msg = "  Usage: /permutate <query>"
            _repl_print_lines(msg)
        else:
            await do_permutate(client, storage, rest)
    elif (rest := _slash_args(line, "/import")) is not None:
        if not rest:
            msg = "  Usage: /import <element>"
        else:
            msg = await do_import_async(storage, rest)
        _repl_print_lines(msg)
    elif (rest := _slash_args(line, "/unfilled")) is not None:
        _repl_print_lines(do_unfilled(storage))
    elif (rest := _slash_args(line, "/fill")) is not None:
        await _fill_missing_recipes_async(storage)
    elif (rest := _slash_args(line, "/prune")) is not None:
        await _prune_orphans_async(storage)
    elif (rest := _slash_args(line, "/export")) is not None:
        _repl_print_lines(do_export(storage, rest or EXPORT_PATH))
    elif (rest := _slash_args(line, "/exhaust")) is not None:
        if not rest:
            msg = "  Usage: /exhaust <query>"
            _repl_print_lines(msg)
        else:
            await do_exhaust(client, storage, rest)
    elif (rest := _slash_args(line, "/combine")) is not None:
        if (pipe_err := _slash_combine_crawl_pipe_error(rest)) is not None:
            _repl_print_lines(pipe_err)
        elif (
            op_err := _slash_combine_crawl_operator_error(rest, "combine")
        ) is not None:
            _repl_print_lines(op_err)
        else:
            parsed = _parse_two_elements(rest)
            if parsed is None:
                msg = "  Usage: /combine <element> <element>"
            else:
                first, second = parsed
                msg = await do_combine(client, storage, first, second)
            _repl_print_lines(msg)
    elif (rest := _slash_args(line, "/crawl")) is not None:
        if (pipe_err := _slash_combine_crawl_pipe_error(rest)) is not None:
            _repl_print_lines(pipe_err)
        elif (op_err := _slash_combine_crawl_operator_error(rest, "crawl")) is not None:
            _repl_print_lines(op_err)
        else:
            parsed = _parse_two_elements(rest)
            if parsed is None:
                msg = "  Usage: /crawl <element> <element>"
                _repl_print_lines(msg)
            else:
                first, second = parsed
                await do_crawl(client, storage, first, second)
    elif (rest := _slash_args(line, "/with")) is not None:
        parsed = _parse_with_args(rest)
        if parsed is None:
            msg = "  Usage: /with <element> <query>"
            _repl_print_lines(msg)
        else:
            element, query = parsed
            await do_with(client, storage, element, query)
    elif (rest := _slash_args(line, "/cross")) is not None:
        if (op_err := _slash_cross_operator_error(rest)) is not None:
            _repl_print_lines(op_err)
        else:
            parsed = _parse_cross_queries(rest)
            if parsed is None:
                msg = "  Usage: /cross <query> <query>"
                _repl_print_lines(msg)
            else:
                left_q, right_q = parsed
                await do_cross(client, storage, left_q, right_q)
    elif line == "/history":
        _repl_print_lines(do_history(storage))
    elif line == "/queue":
        _paint_queue_panel(force=True)
        if _chrome_enabled:
            _repl_print_lines(do_queue_status())
        elif not _current_command and not _command_queue:
            _repl_print_lines(do_queue_status())
    elif line == "/clear":
        if not _chrome_enabled:
            print(f"  {_color('(terminal has no output buffer to clear)', DIM)}")
        else:
            _chrome_sync()
    elif " ++ " in line:
        parts = line.split(" ++ ", 1)
        first = parts[0].strip()
        second = parts[1].strip()
        if not first or not second:
            msg = "  Usage: <element> ++ <element>"
            _repl_print_lines(msg)
        else:
            await do_crawl(client, storage, first, second)
    elif re.search(r"\+\s+\|", line):
        msg = (
            "  Use <element> +| <query> "
            f"(no space between + and |). Type {_color('/help', YELLOW)} for commands."
        )
        _repl_print_lines(msg)
    elif "+|" in line:
        parts = line.split("+|", 1)
        name = parts[0].strip()
        query = parts[1].strip()
        if not name or not query:
            msg = "  Usage: <element> +| <query>"
            _repl_print_lines(msg)
        else:
            await do_with(client, storage, name, query)
    elif " * " in line:
        parts = line.split(" * ", 1)
        left_q = parts[0].strip()
        right_q = parts[1].strip()
        if not left_q or not right_q:
            msg = "  Usage: <query> * <query>"
            _repl_print_lines(msg)
        else:
            await do_cross(client, storage, left_q, right_q)
    elif " + " in line:
        parts = line.split(" + ", 1)
        first = parts[0].strip()
        second = parts[1].strip()
        if not first or not second:
            msg = "  Usage: <element> + <element>"
            _repl_print_lines(msg)
        else:
            res = await do_combine(client, storage, first, second)
            _repl_print_lines(res)
    else:
        _repl_print_lines(f"  Unknown input. Type {_color('/help', YELLOW)} for commands.")


async def _api_worker(client, storage):
    """Process queued API/long-running commands (FIFO)."""
    global _current_command
    global _bulk_confirm_resolved
    global _skip_summary_shown
    while _command_queue:
        _reset_cancelled()
        line = _command_queue.pop(0)
        _current_command = line
        _skip_summary_shown = False
        _bulk_confirm_resolved = not _command_may_bulk_confirm(line)
        _paint_queue_panel()
        _enter_cancel_scope()
        try:
            await _dispatch_line(client, storage, line)
        except CommandCancelled:
            pass
        except Exception as e:
            if not _cancelled:
                err = f"  {_color(f'Error: {e}', RED)}"
                _repl_print_lines(err)
        finally:
            _current_command = None
            _paint_queue_panel()
            _exit_cancel_scope()
            if _cancelled:
                if _discard_queue_after_cancel:
                    discarded = len(_command_queue)
                    _command_queue.clear()
                    if discarded:
                        msg = f"  {_color(f'Cancelled. Discarded {discarded} queued command(s).', DIM)}"
                        _repl_print_lines(msg)
                    _mark_cancel_notified()
                    break
                if not _skip_summary_shown:
                    msg = f"  {_color('Skipped.', YELLOW)}"
                    _repl_print_lines(msg)
                _reset_cancelled()


def _ensure_api_worker(client, storage):
    global _api_worker_task
    if _api_worker_task is None or _api_worker_task.done():
        _api_worker_task = asyncio.create_task(_api_worker(client, storage))


def _enqueue_command_line(line: str, client, storage) -> bool:
    """Append a line to the API queue if allowed. Returns True if enqueued."""
    error = _validate_command_line(line)
    if error:
        _repl_print_lines(error)
        return False
    if line in _command_queue or line == _current_command:
        msg = f"  {_color('Already queued.', DIM)}"
        _repl_print_lines(msg)
        return False
    if len(_command_queue) >= _MAX_QUEUE_DEPTH:
        msg = f"  {_color(f'Queue full (max {_MAX_QUEUE_DEPTH}).', YELLOW)}"
        _repl_print_lines(msg)
        return False
    deferred = _queue_enqueue_deferred()
    if _current_command is None:
        _reset_cancelled()
    _command_queue.append(line)
    _ensure_api_worker(client, storage)
    if deferred and not _chrome_enabled:
        msg = f"  {_color(f'Queued: {_sanitize_queue_line(line)}', DIM)}"
        print(msg)
    _chrome_sync()
    return True


async def _shutdown_interactive() -> int:
    """Cancel worker, discard queue, and print goodbye (shared by /quit and interrupts)."""
    global _cancelled, _command_queue, _api_worker_task
    _cancelled = True
    discarded = len(_command_queue)
    _command_queue.clear()
    if _api_worker_task is not None and not _api_worker_task.done():
        _api_worker_task.cancel()
        try:
            await asyncio.wait_for(_api_worker_task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    if discarded:
        msg = f"  Discarded {discarded} queued command(s)."
        _repl_print_lines(msg)
    _repl_print_lines("Goodbye!")
    return discarded


async def interactive_mode():
    global _command_queue, _current_command, _api_worker_task, _cancelled
    global _confirm_future, _last_queue_snapshot, _queue_panel_height
    global _interactive_mode_active, _confirm_expected, _bulk_confirm_pending
    global _bulk_confirm_resolved
    _interactive_mode_active = True
    _tty_reset_stdin_reader()
    _confirm_expected = False
    _bulk_confirm_pending = False
    _bulk_confirm_resolved = True
    _command_queue = []
    _current_command = None
    _api_worker_task = None
    _confirm_future = None
    _last_queue_snapshot = ""
    _queue_panel_height = 0

    print(_color("=== Infinite Craft CLI ===", BOLD + CYAN))
    print()

    storage = DiscoveryStorage(DISCOVERIES_PATH)
    _patch_repl_print(True)
    _chrome_enable()
    try:
        async with InfiniteCraftClient(
            rate_limit=API_RATE_LIMIT,
            cancel_check=lambda: _cancelled,
            rate_limit_sleep_step=_RATE_LIMIT_SLEEP_STEP,
        ) as client:
            starters = "  ".join(format_element(e) for e in storage.get_all()[:4])
            _repl_print_lines(f"  Starting elements: {starters}")
            total = len(storage.get_all())
            _repl_print_lines(f"  Discovered: {_color(str(total), GREEN)} elements")
            _repl_print_lines(f"  Type {_color('/help', YELLOW)} for commands")

            while True:
                _paint_queue_panel()

                if _command_queue and _current_command is None:
                    await asyncio.sleep(0)
                    continue

                if _awaiting_bulk_confirm_setup():
                    await asyncio.sleep(0)
                    continue

                if _waiting_for_confirm():
                    try:
                        line = await _prompt_input(_craft_prompt())
                    except (EOFError, KeyboardInterrupt):
                        if _confirm_future is not None and not _confirm_future.done():
                            _confirm_future.set_result("")
                    else:
                        if _is_local_command(line):
                            await _dispatch_line(client, storage, line)
                            continue
                        if _route_confirm_input(line):
                            pass
                        elif line.strip():
                            _enqueue_command_line(line, client, storage)
                    continue

                try:
                    line = await _prompt_input(_craft_prompt())
                except (EOFError, KeyboardInterrupt):
                    _repl_print("")
                    await _shutdown_interactive()
                    break

                if not line:
                    if _route_confirm_input(""):
                        continue
                    continue

                if line in ("/quit", "/exit"):
                    await _shutdown_interactive()
                    break

                if _is_local_command(line):
                    await _dispatch_line(client, storage, line)
                    continue

                if _route_confirm_input(line):
                    continue

                _enqueue_command_line(line, client, storage)
    finally:
        _patch_repl_print(False)
        _chrome_disable()
        _interactive_mode_active = False


# ---------------------------------------------------------------------------
# Non-interactive CLI
# ---------------------------------------------------------------------------
async def noninteractive_mode(args):
    # Commands that only need storage (no API)
    storage_only_commands = {
        "search",
        "list",
        "recipe",
        "unfilled",
        "export",
        "fill",
        "prune",
        "import_cmd",
    }

    if args.command in storage_only_commands:
        storage = DiscoveryStorage(DISCOVERIES_PATH)
        if args.command == "search":
            print(do_search(storage, args.query))
        elif args.command == "list":
            print(do_list(storage))
        elif args.command == "recipe":
            print(do_recipe(storage, args.name))
        elif args.command == "unfilled":
            print(do_unfilled(storage))
        elif args.command == "export":
            path = args.path if args.path else EXPORT_PATH
            print(do_export(storage, path))
        elif args.command == "fill":
            await _fill_missing_recipes_async(storage)
        elif args.command == "prune":
            await _prune_orphans_async(storage)
        elif args.command == "import_cmd":
            print(await do_import_async(storage, args.source))
    else:
        # Commands that need the API client
        storage = DiscoveryStorage(DISCOVERIES_PATH)
        async with InfiniteCraftClient(rate_limit=API_RATE_LIMIT) as client:
            if args.command == "combine":
                print(await do_combine(client, storage, args.first, args.second))
            elif args.command == "exhaust":
                await do_exhaust(client, storage, args.query)
            elif args.command == "crawl":
                await do_crawl(client, storage, args.first, args.second)
            elif args.command == "permute":
                await do_permute(client, storage, args.query)
            elif args.command == "permutate":
                await do_permutate(client, storage, args.query)
            elif args.command == "cross":
                await do_cross(client, storage, args.left, args.right)
            elif args.command == "with":
                await do_with(client, storage, args.element, args.query)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Infinite Craft CLI")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command")

    combine_p = subparsers.add_parser("combine", help="Combine two elements")
    combine_p.add_argument("first", help="First element name")
    combine_p.add_argument("second", help="Second element name")

    search_p = subparsers.add_parser("search", help="Search discovered elements")
    search_p.add_argument("query", help=_QUERY_HELP)

    subparsers.add_parser("list", help="List all discovered elements")

    recipe_p = subparsers.add_parser(
        "recipe", help="Show shortest recipe from base elements"
    )
    recipe_p.add_argument("name", help="Element name")

    import_p = subparsers.add_parser(
        "import", help="Import from Infinibrowser or .ic save file"
    )
    import_p.add_argument("source", help="Element name or path to .ic file")
    import_p.set_defaults(command="import_cmd")

    export_p = subparsers.add_parser(
        "export", help="Export discoveries as .ic save file"
    )
    export_p.add_argument(
        "path", nargs="?", default=None, help="Output path (optional)"
    )

    subparsers.add_parser("fill", help="Fetch missing recipes from Infinibrowser")

    subparsers.add_parser("unfilled", help="List elements without recipes")

    subparsers.add_parser(
        "prune", help="Remove orphan elements Infinibrowser can't fill"
    )

    exhaust_p = subparsers.add_parser(
        "exhaust", help="Each query match combined with all discoveries"
    )
    exhaust_p.add_argument("query", help=_QUERY_HELP)

    crawl_p = subparsers.add_parser("crawl", help="Combine two elements and crawl")
    crawl_p.add_argument("first", help="First element name")
    crawl_p.add_argument("second", help="Second element name")

    permute_p = subparsers.add_parser(
        "permute", help="Combine all matching elements with each other"
    )
    permute_p.add_argument("query", help=_QUERY_HELP)

    permutate_p = subparsers.add_parser(
        "permutate", help="Permute repeatedly until no new discoveries"
    )
    permutate_p.add_argument("query", help=_QUERY_HELP)

    cross_p = subparsers.add_parser(
        "cross", help="Cross-combine matches from two queries"
    )
    cross_p.add_argument("left", help=_QUERY_HELP)
    cross_p.add_argument("right", help=_QUERY_HELP)

    with_p = subparsers.add_parser(
        "with", help="Combine element with all matching discoveries"
    )
    with_p.add_argument("element", help="Element name")
    with_p.add_argument("query", help=_QUERY_HELP)

    args = parser.parse_args()

    if args.command is None:
        asyncio.run(interactive_mode())
    else:
        asyncio.run(noninteractive_mode(args))


if __name__ == "__main__":
    main()
