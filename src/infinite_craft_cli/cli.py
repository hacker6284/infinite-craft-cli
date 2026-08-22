#!/usr/bin/env python3
"""Infinite Craft CLI — combine elements from the terminal or as a scripted tool."""

import asyncio
import argparse
from pathlib import Path
import builtins
import contextlib
import gzip
import re
import json
import os
import select
import shutil
import signal
import sys
import tempfile
import unicodedata
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
from infinite_craft_cli import relay as relay_client
from infinite_craft_cli.client import (
    InfiniteCraftClient,
    NealRateLimited,
    fetch_json,
    ib_get,
)
from infinite_craft_cli.ratelimit import RateLimitCancelled
from infinite_craft_cli.storage import DiscoveryStorage
from infinite_craft_cli import __version__

from infinite_craft_cli.data import DISCOVERIES_PATH, RECIPES_PATH, EXPORT_PATH
from infinite_craft_cli._sudo import craft

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
_MAX_IC_COMPRESSED_BYTES = 32 * 1024 * 1024
_MAX_IC_DECOMPRESSED_BYTES = 64 * 1024 * 1024
_MAX_IC_ITEMS = 50_000
_RATE_LIMIT_SLEEP_STEP = 0.05
_QUERY_HELP = "Search query (wildcards, /regex/, ! exclude, ^ first discoveries)"

# Session-only history
_history: list[tuple[str, str, str]] = []
_session_input_history: list[str] = []

# Interactive command queues — pair (neal.fun) and IB (Infinibrowser) are independent.
_command_queue: list[str] = []  # pair-API lane queue
_ib_command_queue: list[str] = []
_current_command: str = ""  # pair lane currently running; "" = idle
_current_ib_command: str = ""  # IB lane; "" = idle
_api_worker_task: asyncio.Task | None = None  # pair worker
_ib_worker_task: asyncio.Task | None = None
_MAX_QUEUE_DEPTH = 50
_stdin_lock = asyncio.Lock()
_cancel_scope_depth = 0
_sigint_previous: object | None = None
_winch_previous: object | None = None
_confirm_future: asyncio.Future[str] | None = None
_confirm_answer_buffer: str | None = None
_last_queue_snapshot: str = ""
_queue_panel_height: int = 0
_interactive_mode_active: bool = False
_confirm_expected: bool = False
_bulk_confirm_pending: bool = False
_bulk_confirm_resolved: bool = True
# TTY chrome: streaming output scrolls above a pinned queue + prompt (trainer.js layout).
_chrome_enabled: bool = False
_chrome_prompt: str = ""
_chrome_input_active: bool = False
_chrome_partial: str = ""
_chrome_last_reserve: int = 0
_chrome_last_height: int = 0
_repl_print_patched: bool = False
_repl_print_lock = threading.RLock()
_chrome_last_state: object = None
_tty_stdin_unread: list[str] = []
# Total time budget to collect CSI/SS3 bytes after ESC before lone-Escape (skip).
_ESC_SEQUENCE_WAIT_S = 0.3
_CSI_POLL_INTERVAL_S = 0.02
_builtin_print = builtins.print

# Prompt strings (extracted consts; used by _craft_prompt and chrome)
CRAFT_PROMPT = "craft> "
CONFIRM_PROMPT = "confirm [y/n]> "
# Segmented rate bar: left 1/2 = next-slot wait refill, right 1/2 = remaining.
_RATE_BAR_LEFT = 9
_RATE_BAR_RIGHT = 9
_RATE_TICK_SECONDS = 0.3

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
    global _ib_command_queue, _current_ib_command, _ib_worker_task
    global _current_command, _api_worker_task, _confirm_future, _confirm_answer_buffer
    global _last_queue_snapshot, _queue_panel_height, _interactive_mode_active
    global _confirm_expected, _bulk_confirm_pending, _bulk_confirm_resolved
    global _chrome_enabled, _chrome_prompt, _chrome_input_active, _chrome_partial
    global _chrome_last_reserve, _chrome_last_state, _repl_print_patched
    global _tty_stdin_unread, _cancelled, _cancel_scope_depth, _rate_limit_waiting
    global \
        _discard_queue_after_cancel, \
        _skip_summary_shown, \
        _sigint_previous, \
        _winch_previous
    global _tty_read_byte_hook, _test_prompt_input_hook
    _pair_cache.clear()
    _history.clear()
    _session_input_history.clear()
    _command_queue.clear()
    _ib_command_queue.clear()
    _current_command = ""
    _current_ib_command = ""
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
    _chrome_last_height = 0
    _chrome_last_state = None
    if _repl_print_patched:
        with contextlib.suppress(Exception):
            _patch_repl_print(False)
        _repl_print_patched = False
    # use central teardown for chrome/tty/winch (idempotent)
    with contextlib.suppress(Exception):
        _teardown_tty_and_chrome()
    _tty_stdin_unread = []
    _cancelled = False
    _cancel_scope_depth = 0
    _rate_limit_waiting = False
    _discard_queue_after_cancel = False
    global _job_done, _job_total, _ib_job_done, _ib_job_total
    global _last_pair, _active_client, _rate_ticker_task, _target_element
    global _confirm_reason, _auto_approve, _script_new_reg
    _job_done = 0
    _job_total = 0
    _ib_job_done = 0
    _ib_job_total = 0
    _last_pair = None
    _active_client = None
    _rate_ticker_task = None
    _target_element = ""
    _auto_approve = False
    global _relay_user_on, _relay_reachable, _relay_hits, _relay_contributed
    global _relay_seeded, _relay_warmup_task
    _relay_user_on = _relay_default_on()
    _relay_reachable = None
    _relay_hits = 0
    _relay_contributed = 0
    _relay_seeded = False
    _relay_warmup_task = None
    _relay_bg_tasks.clear()
    global _cooldown_until, _cooldown_strikes, _bounty_task
    global _bounties_worked, _bounty_progress, _run_id, _run_posted_once
    _cooldown_until = 0.0
    _cooldown_strikes = 0
    _bounty_task = None
    _bounties_worked = 0
    _bounty_progress = None
    _run_id = ""
    _run_posted_once = False
    relay_client.last_hive["peers"] = 0
    relay_client.last_hive["cooledUntil"] = 0
    _script_new_reg = []
    _confirm_reason = ""
    _skip_summary_shown = False
    _sigint_previous = None
    _winch_previous = None
    _tty_read_byte_hook = None
    _test_prompt_input_hook = None
    # ensure no stray worker (full cancel+await via helper if possible from sync ctx; drain to avoid orphan)
    # Always centralize on _cancel_and_await_worker (idempotent, does cancel+wait_for+set None).
    # Assumption for reset: called from sync test fixture/finalizer thread. If get_running_loop succeeds,
    # use threadsafe+await to fully reap worker from the running loop (prevents orphans on test exit/KI).
    # If no running loop (common), just clear ref (the run_until_quit finally or interactive finally already
    # awaited the task; task from dead loop would be orphan anyway).
    try:
        loop = asyncio.get_running_loop()
        fut = asyncio.run_coroutine_threadsafe(_cancel_and_await_worker(), loop)
        fut.result(timeout=1)
    except RuntimeError:
        # no running loop: still attempt cancel on the task ref (best-effort, marks cancelled)
        # before nulling; prevents orphan ref even if loop is dead.
        for name in ("_api_worker_task", "_ib_worker_task"):
            t = globals().get(name)
            if t and not t.done():
                with contextlib.suppress(Exception):
                    t.cancel()
            globals()[name] = None
        # sweep not possible w/o running loop; harness paths cover asyncio.all_tasks cases
    except Exception:
        # best-effort: detach ref, do not let cleanup raise
        for name in ("_api_worker_task", "_ib_worker_task"):
            t = globals().get(name)
            if t and not getattr(t, "done", lambda: True)():
                with contextlib.suppress(Exception):
                    t.cancel()
            globals()[name] = None
    # helper or above always ensures cleared to prevent re-use/orphans
    with contextlib.suppress(Exception):
        _remove_winch_handler()


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
        # Attempt simple repair for common truncation at end (e.g. interrupted write)
        try:
            with open(RECIPES_PATH, encoding="utf-8") as f:
                c = f.read().rstrip()
            repaired = json.loads(c + "\n}\n")
            _save_recipes(repaired)
            return repaired
        except Exception:
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


def _record_recipes_batch(entries: list[tuple[str, str, str]]):
    """Record multiple recipes with a single disk write."""
    if not entries:
        return
    recipes = _load_recipes()
    total_before = sum(len(v) for v in recipes.values())
    craft.record_recipes_batch(recipes, entries)
    total_after = sum(len(v) for v in recipes.values())
    if total_after != total_before:
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


def _tty(text: str) -> str:
    """Sanitize untrusted text when writing to an interactive terminal."""
    if sys.stdout.isatty():
        return _sanitize_queue_line(text)
    return text


def format_element(elem) -> str:
    raw = str(elem)  # uses Element.__str__ which handles emoji
    s = _sanitize_queue_line(raw) if sys.stdout.isatty() else raw
    if elem.is_first_discovery:
        s += " " + _color("[FIRST DISCOVERY!]", BOLD + MAGENTA)
    return s


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------
def _elements_to_boundary(elements) -> list[tuple[str, str, bool]]:
    """Convert Element/MockElement objects to the (name, emoji, first)
    3-tuples the generated kernel adapter expects at the host boundary.
    emoji/first are coerced from None to "" / False (the kernel has no
    nullable fields); this never changes display behavior since both call
    sites treat None and ""/False as equivalently falsy."""
    return [(e.name, e.emoji or "", bool(e.is_first_discovery)) for e in elements]


def _pairs_from_boundary(pair_tuples, elements):
    """Map kernel pair-boundary 6-tuples back to the host's Element objects."""
    by_name = {e.name: e for e in elements}
    return [(by_name[an], by_name[bn]) for an, _ae, _af, bn, _be, _bf in pair_tuples]


def _resolve_element(storage, name: str):
    """Look up an element by name in discoveries; fall back to bare Element."""
    resolved_name, _emoji, _first = craft.resolve_element_boundary(
        _elements_to_boundary(storage.get_all()), name
    )
    found = storage.get_by_name(resolved_name)
    if found is not None:
        return found
    return Element(name=resolved_name)


# Runtime cache for pair results — avoids re-hitting the API for the same combo
_pair_cache: dict[tuple[str, str], Element] = {}


def _raise_if_cancelled() -> None:
    if _cancelled:
        raise CommandCancelled()


async def _cached_pair(client, storage, a, b, *, skip_relay_lookup: bool = False):
    """Wrapper around client.pair that caches results by sorted element names.

    Cache tiers: local run cache → hive-mind relay → neal.fun. A neal slot
    is committed only after both cache tiers miss; fresh neal results are
    contributed back to the hive in the background."""
    global _relay_hits
    _raise_if_cancelled()
    key = craft.pair_key(a.name, b.name)
    if key in _pair_cache:
        return _pair_cache[key]
    # skip_relay_lookup: the caller (a bulk run) already swept this whole
    # batch against the hive, so a per-pair lookup here would just re-miss.
    if _relay_active() and not skip_relay_lookup:
        found = await asyncio.to_thread(relay_client.lookup, [(a.name, b.name)])
        if found is None:
            _relay_mark_unreachable()
        else:
            _relay_apply_hive(client)
            hit = found.get(f"{key[0]}\0{key[1]}")
            if hit is not None:
                r, e = hit
                result = Element(name=r, emoji=e or "", is_first_discovery=False)
                _pair_cache[key] = result
                _relay_hits += 1
                if r is not None:
                    _record_recipes_batch([(r, a.name, b.name)])
                return result
    if _cooling():
        raise NealRateLimited()
    for attempt in range(craft.pair_retry_max_attempts()):
        _raise_if_cancelled()
        try:
            result = await client.pair(a.name, b.name)
            break
        except RateLimitCancelled:
            raise CommandCancelled() from None
        except NealRateLimited:
            _trip_cooldown()
            raise
        except Exception:
            if not craft.pair_should_retry(attempt):
                raise
            if await _sleep_cancellable_async(craft.pair_retry_backoff_ms(attempt) / 1000.0):
                raise CommandCancelled()
    _pair_cache[key] = result
    if result.name is not None:
        _record_recipes_batch([(result.name, a.name, b.name)])
    _relay_contribute_bg(a.name, b.name, result)
    return result


async def do_combine(client, storage, first_name: str, second_name: str) -> str:
    first = _resolve_element(storage, first_name)
    second = _resolve_element(storage, second_name)
    _set_lane_progress("pair", 0, 1)
    _set_last_pair(first.name, second.name)
    try:
        result = await _cached_pair(client, storage, first, second)
        _set_lane_progress("pair", 1, 1)
    except CommandCancelled:
        raise
    except Exception as e:
        return _color(f"  Error: {_tty(str(e))}", RED)
    # If the pairing succeeded, ensure both inputs and result are in discoveries
    if result.name is not None:
        for elem in (first, second):
            storage.add(
                name=craft.sanitize_element_name(elem.name),
                emoji=elem.emoji,
                is_first_discovery=False,
            )
        storage.add(
            name=craft.sanitize_element_name(result.name),
            emoji=result.emoji,
            is_first_discovery=result.is_first_discovery,
        )
    result_display = result.name if result.name else "Nothing"
    _history.append((first_name.strip(), second_name.strip(), result_display))
    # format_element on operands and result preserves FIRST tag color/text.
    if result.name is None:
        res = _color("Nothing", DIM)
    else:
        res = format_element(result)
    line = f"  {format_element(first)} + {format_element(second)} = {res}"
    hit = craft.is_target_hit(_target_element, result.name or "")
    if hit:
        line += " " + _color("★ TARGET ★", BOLD + YELLOW + MAGENTA)
        stop = await _acknowledge_target_hit(first.name, second.name, result.name or "")
        if stop:
            raise CommandCancelled()
    return line


def _match_elements(storage, query: str) -> tuple[list[Element], str | None]:
    """Return (matches, error_message) for discovered elements matching a query."""
    discoveries = storage.get_all()
    match_tuples, err = craft.match_elements_boundary(
        _elements_to_boundary(discoveries), query
    )
    if err:
        return [], err
    by_name = {e.name: e for e in discoveries}
    matches = [by_name[n] for (n, _emoji, _first) in match_tuples]
    return matches, None


def do_search(storage, query: str) -> str:
    matches, err = _match_elements(storage, query)
    if err:
        return f"  {err}"
    if not matches:
        return "  No matches found."
    return "\n".join(f"  {format_element(e)}" for e in matches)


def _trace_recipe(storage, name: str) -> tuple[int, str, list[tuple[str, str, str]]]:
    """Pure trace-recipe core: kernel BFS result as (status, target, steps).
    status: 0=NotFound 1=IsBase 2=NoRecipe 3=Unreachable 4=Steps (see
    craft.sudo RecipeResult / recipe_result_to_tuple). Also used directly by
    tests/parity/run_py.py's host-parity harness — keep this exact name and
    signature."""
    recipes = _load_recipes()
    elements = _elements_to_boundary(storage.get_all())
    return craft.trace_recipe_boundary(elements, recipes, name)


def do_recipe(storage, name: str) -> str:
    """Show shortest recipe tree for an element via BFS on local recipes."""
    status, target, steps = _trace_recipe(storage, name)
    if status == 0:  # NotFound
        return f"  {format_element(Element(name=name.strip()))} not found in discoveries."
    if status == 1:  # IsBase
        return f"  {format_element(Element(name=target))} is a base element."
    if status == 2:  # NoRecipe
        return f"  No recipe known for {format_element(Element(name=target))}. Try /fill or /import."
    if status == 3:  # Unreachable
        return f"  Cannot trace full lineage for {format_element(Element(name=target))} — missing intermediate recipes."

    # status == 4: Steps
    t_elem = storage.get_by_name(target)
    t_str = (
        format_element(t_elem)
        if t_elem
        else _color(format_element(Element(name=target)), BOLD)
    )
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
            lines.append(
                f"  {i}. {format_element(a_elem)} + {format_element(b_elem)} = {r_str}"
            )
        else:
            lines.append(f"  {i}. {_tty(a)} + {_tty(b)} = {_tty(r)}")
    return "\n".join(lines)


async def do_crawl(client, storage, first_name: str, second_name: str):
    """Combine two elements, then iteratively combine results with all inputs until nothing new."""
    # Crawl never uses bulk confirm; resolve immediately so the interactive loop
    # does not stall in _awaiting_bulk_confirm_setup (belt-and-suspenders vs
    # may_bulk_confirm excluding /crawl).
    global _bulk_confirm_resolved
    _bulk_confirm_resolved = True

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
                _repl_print_lines("  Stopped early.")
                _mark_cancel_notified()
            break
        generation += 1
        pool_elements = list(pool.values())
        pair_tuples, new_keys = craft.crawl_generation_pairs_boundary(
            _elements_to_boundary(pool_elements), list(tried)
        )
        new_pairs = _pairs_from_boundary(pair_tuples, pool_elements)
        tried.update(new_keys)

        if not new_pairs:
            _repl_print_lines(f"  Exhausted all pairs. {len(pool)} elements in pool.")
            break

        _repl_print_lines(
            f"  --- Generation {generation}: {len(new_pairs)} new pairs to try ---"
        )

        # Snapshot pool names before running pairs
        before = set(pool.keys())
        await _combine_pairs(client, storage, new_pairs)

        # Check pair cache for new elements produced this generation
        new_elements = []
        for a, b in new_pairs:
            key = craft.pair_key(a.name, b.name)
            result = _pair_cache.get(key)
            if result and result.name and result.name not in pool:
                pool[result.name] = result
                new_elements.append(result)

        new_count = len(new_elements)
        _repl_print_lines(f"  +{new_count} new ({len(pool)} in pool)")

        if new_count == 0 or _cancelled:
            if _cancelled:
                if not _skip_summary_shown:
                    _repl_print_lines("  Stopped early.")
                    _mark_cancel_notified()
            else:
                _repl_print_lines("  No new discoveries. Stopping.")
            break

    _repl_print_lines(f"  Final pool ({len(pool)}):")
    for name in sorted(pool.keys()):
        _repl_print_lines(f"    {format_element(pool[name])}")


async def do_lucky(client, storage, count: int, seed: int | None = None):
    """Try random untried pairs — entropy from the neglected pair space."""
    if count <= 0:
        _repl_print_lines("  Usage: /lucky [count] (count must be positive)")
        return
    if seed is None:
        seed = int(time.time() * 1000) % 2147483648
    tried = [f"{ka}\0{kb}" for (ka, kb) in _pair_cache.keys()]
    raw = craft.lucky_pairs_boundary(
        _elements_to_boundary(storage.get_all()), _load_recipes(), tried, count, seed
    )
    pairs = _script_pairs_from_raw(raw)
    if not pairs:
        _repl_print_lines("  No untried pairs found — the space may be exhausted.")
        return
    note = "" if len(pairs) >= count else f" (only {len(pairs)} untried found)"
    plural = "" if len(pairs) == 1 else "s"
    _repl_print_lines(f"  Feeling lucky: {len(pairs)} random untried pair{plural}...{note}")
    await _confirm_and_run_pairs(client, storage, pairs)


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
    pair_tuples = craft.exhaust_pairs_boundary(
        _elements_to_boundary(matches), _elements_to_boundary(all_elements)
    )
    pairs: list[tuple] = _pairs_from_boundary(pair_tuples, all_elements)
    if not pairs:
        _repl_print_lines(f"  No valid pairs for query: {query}")
        return

    _repl_print_lines(
        f"  Exhausting {len(matches)} element{'' if len(matches) == 1 else 's'} matching {_color(query, YELLOW)} "
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
    pairs = _pairs_from_boundary(
        craft.with_pairs_boundary(
            _elements_to_boundary([target])[0], _elements_to_boundary(others)
        ),
        [target] + list(others),
    )
    if not pairs:
        _repl_print_lines(f"  No other elements match: {query}")
        return
    _repl_print_lines(
        f"  Combining {_color(format_element(target), BOLD)} with {len(pairs)} elements "
        f"matching {_color(query, YELLOW)}..."
    )
    await _confirm_and_run_pairs(client, storage, pairs)


def _render_error_segments(segments) -> str:
    """Render kernel (text, highlight) error segments with ANSI color."""
    return "".join(
        _color(text, YELLOW) if highlight else text for text, highlight in segments
    )


_BULK_WARN_THRESHOLD = 200


_cancelled = False
_discard_queue_after_cancel = False
_skip_summary_shown = False
_rate_limit_waiting = False
# Sticky chrome: pair job progress + last pair; IB job progress separate
_job_done: int = 0
_job_total: int = 0
_ib_job_done: int = 0
_ib_job_total: int = 0
_last_pair: tuple[str, str] | None = None
_active_client = None  # InfiniteCraftClient | None
_rate_ticker_task: asyncio.Task | None = None
# /target: pause batch when this element name is produced by a combination.
# Empty string = no target (kernel is_target_hit/apply_target_state treat "" as idle).
_target_element: str = ""
_auto_approve: bool = False  # /auto: skip bulk-size y/n confirms this session
_script_new_reg: list[tuple[str, str, bool]] = []  # the [] register (session-global)
# Hive-mind relay (shared pair-result cache) session state. The tier is
# consulted only when the user toggle is on AND the last ping succeeded;
# everything fails open to plain neal.fun behavior.


def _relay_default_on() -> bool:
    """IC_RELAY=off disables the hive tier at startup (tests, air-gapped)."""
    return os.environ.get("IC_RELAY", "on").strip().lower() not in (
        "off",
        "0",
        "no",
        "false",
    )


_relay_user_on: bool = _relay_default_on()  # /relay session toggle
_relay_reachable: bool | None = None  # None = not yet pinged (warming)
_relay_hits: int = 0  # pairs served from the hive this session
_relay_contributed: int = 0  # entries the relay hadn't seen, from us
_relay_seeded: bool = False  # one re-seed upload per session
_relay_warmup_task: asyncio.Task | None = None
_relay_bg_tasks: set = set()
# 429 cooldown: neal's 429 is an hours-long IP ban. While cooling, zero neal
# requests (hive lookups still fine); the state is broadcast via the relay so
# every session on this IP stands down too.
_cooldown_until: float = 0.0  # epoch seconds
_cooldown_strikes: int = 0
# Bounty worker (serve the hive while idle at the prompt)
_bounty_task: asyncio.Task | None = None
_bounties_worked: int = 0
_bounty_progress: tuple[int, int] | None = None  # (done, batch) while serving
# Active pair-lane run id, asserted in every beat. A run's bounties live on
# the relay exactly as long as this id keeps appearing — clearing it (the
# finally in _combine_pairs) IS the cancel mechanism; no revoke call exists.
_run_id: str = ""
_run_seq: int = 0
_run_posted_once: bool = False  # the 🐝 posted line prints once per run


def _cooling() -> bool:
    return time.time() < _cooldown_until


def _trip_cooldown() -> None:
    """A genuine 429 arrived: stand down for hours (doubling per strike)."""
    global _cooldown_until, _cooldown_strikes
    # Concurrent pairs in one gather batch can each raise 429 from a single
    # ban event — only the first counts as a strike, or the duration inflates.
    if _cooling():
        return
    _cooldown_strikes += 1
    _cooldown_until = time.time() + craft.cooldown_duration_ms(_cooldown_strikes) / 1000.0
    resume = time.strftime("%H:%M", time.localtime(_cooldown_until))
    _repl_print_lines(
        f"  {_color('429 from neal.fun — standing down until ~' + resume, RED)} "
        f"(the ban is IP-wide and lasts hours; hive lookups still work)"
    )


def _relay_apply_hive(client) -> None:
    """Fold the latest hive envelope into local state: split the per-IP
    budget by spending peers, and adopt a sibling session's cooldown."""
    global _cooldown_until
    peers = int(relay_client.last_hive.get("peers") or 0)
    if client is not None:
        limiter = client._rate_limiter
        limiter.set_effective_max(
            craft.effective_rate_limit(limiter.base_max, max(1, peers))
        )
    cu = int(relay_client.last_hive.get("cooledUntil") or 0) / 1000.0
    now = time.time()
    if cu > now:
        # Clamp a relay-reported cooldown to the kernel maximum: a garbage or
        # clock-skewed relay must never be able to park us offline forever
        # (fail-open). The relay clamps too, but never trust it blindly.
        cu = min(cu, now + craft.cooldown_duration_ms(3) / 1000.0)
        if cu > _cooldown_until:
            _cooldown_until = cu
_target_hit_lock = asyncio.Lock()
# Confirm chrome reason (e.g. "331 pairs"); keys live only on the prompt.
_confirm_reason: str = ""


def _reset_cancelled():
    global _cancelled, _discard_queue_after_cancel, _skip_summary_shown
    _cancelled = False
    _discard_queue_after_cancel = False
    _skip_summary_shown = False


def _mark_cancel_notified() -> None:
    """Record that the running command already printed a cancel/stop summary."""
    global _skip_summary_shown
    _skip_summary_shown = True


def _rate_limit_wait_callback(waiting: bool) -> None:
    """Sync callback for RateLimiter.acquire (start/end of backoff sleep chunks).

    Sets transient flag read by queue display; forces refresh via existing
    _chrome_sync / _paint_queue_panel (throttled, non-janky). Clear on exit.
    """
    global _rate_limit_waiting
    if _rate_limit_waiting != waiting:
        _rate_limit_waiting = waiting
        if _chrome_enabled:
            _chrome_sync()
        else:
            _paint_queue_panel(force=True)


def _relay_active() -> bool:
    return _relay_user_on and _relay_reachable is True


def _relay_restore_budget() -> None:
    """Return the limiter to its full per-IP budget. Called whenever the hive
    tier stops arbitrating (relay unreachable, or /relay off) so a session
    isn't left throttled to a split that no longer applies."""
    if _active_client is not None:
        _active_client._rate_limiter.set_effective_max(
            _active_client._rate_limiter.base_max
        )


def _relay_mark_unreachable() -> None:
    global _relay_reachable
    _relay_reachable = False
    _relay_restore_budget()


async def _relay_warmup(storage) -> None:
    """Ping the relay (wakes a spun-down free instance), then re-seed the
    hive once per session from this save's recipe store. Never raises — a
    warmup failure must not surface as an unretrieved-task error."""
    try:
        await _relay_warmup_inner(storage)
    except Exception:
        _relay_reachable_false_safe()


def _relay_reachable_false_safe() -> None:
    global _relay_reachable
    _relay_reachable = False


async def _relay_warmup_inner(storage) -> None:
    global _relay_reachable, _relay_seeded, _relay_contributed
    health = await asyncio.to_thread(relay_client.ping)
    if health is None:
        # Free instances cold-start in tens of seconds; one more try.
        await asyncio.sleep(20)
        health = await asyncio.to_thread(relay_client.ping)
    _relay_reachable = health is not None
    if not _relay_reachable or _relay_seeded or not _relay_user_on:
        return
    entries = [
        tuple(t)
        for t in craft.relay_reseed_entries(
            [
                (e.name, e.emoji or "", bool(e.is_first_discovery))
                for e in storage.get_all()
            ],
            _load_recipes(),
        )
    ]
    if entries:
        added = await asyncio.to_thread(relay_client.contribute, entries)
        if added is None:
            _relay_reachable = False
            return
        _relay_contributed += added
    _relay_seeded = True


def _relay_spawn_warmup(storage) -> None:
    global _relay_warmup_task
    if not _relay_user_on:
        return
    if _relay_warmup_task is not None and not _relay_warmup_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _relay_warmup_task = loop.create_task(_relay_warmup(storage))


def _merge_hive_results(found: dict, pair_names: list[tuple[str, str]]) -> int:
    """Insert relay lookup hits into the local pair cache. Returns merges."""
    global _relay_hits
    merged = 0
    for a_name, b_name in pair_names:
        ka, kb = craft.pair_key(a_name, b_name)
        if (ka, kb) in _pair_cache:
            continue
        v = found.get(f"{ka}\0{kb}")
        if v is None:
            continue
        r, e = v
        _pair_cache[(ka, kb)] = Element(name=r, emoji=e or "", is_first_discovery=False)
        _relay_hits += 1
        merged += 1
        if r is not None:
            _record_recipes_batch([(r, a_name, b_name)])
    return merged


async def _hive_sweep(client, pairs) -> int:
    """Batch-lookup every locally-missing pair against the hive; merge hits.

    ``pairs`` is [(ElementA, ElementB), ...]. Returns merged-hit count."""
    if not _relay_active():
        return 0
    missing = [
        (a.name, b.name)
        for (a, b) in pairs
        if craft.pair_key(a.name, b.name) not in _pair_cache
    ]
    if not missing:
        return 0
    found = await asyncio.to_thread(relay_client.lookup, missing)
    if found is None:
        _relay_mark_unreachable()
        return 0
    _relay_apply_hive(client)
    return _merge_hive_results(found, missing)


async def _hive_run_sync(client, remaining) -> None:
    """Batch-event hive sync — there is no timer behind it. Absorbs fills
    for the pairs we're about to spend on (lookup on the head) and offers
    everything beyond the slots free right now to the board (bound to this
    run's id — the board entry lives exactly as long as our beats keep
    asserting that id). Called at run start and before spending after a
    rate wait."""
    global _run_posted_once
    if not _relay_active():
        return
    missing = [
        (a.name, b.name)
        for (a, b) in remaining
        if craft.pair_key(a.name, b.name) not in _pair_cache
    ]
    if not missing:
        return
    left, _max, _f = client._rate_limiter.chrome_snapshot()
    start, count = craft.bounty_sync_plan(len(missing), left)
    head = missing[:start]
    if head:
        found = await asyncio.to_thread(relay_client.lookup, head)
        if found is None:
            _relay_mark_unreachable()
            return
        _relay_apply_hive(client)
        _merge_hive_results(found, head)
    if count > 0:
        resp = await asyncio.to_thread(
            relay_client.sync_bounties, missing[start : start + count], _run_id
        )
        if resp is None:
            _relay_mark_unreachable()
            return
        _merge_hive_results(resp["results"], missing)
        posted = resp.get("posted") or 0
        if posted and not _run_posted_once:
            _run_posted_once = True
            _repl_print_lines(
                _color(f"  🐝 posted {posted} bounties to the hive", DIM)
            )


async def _hive_wait_for_slots(client, batch, remaining) -> None:
    """Event-driven rate wait: while this mini-batch has misses we lack
    slots for, sleep until the next slot frees (the sliding window makes
    the wake time exactly computable; the sleep is cancellable), then
    sync-before-spend — so a freed slot is never burned on a pair the
    fleet already answered. Cancellation and 429 cooldown exit
    immediately; the beat task keeps liveness flowing throughout."""
    waited = False
    while not _cancelled and not _cooling() and _relay_active():
        misses = [
            p
            for p in batch
            if craft.pair_key(p[0].name, p[1].name) not in _pair_cache
        ]
        if not misses:
            return  # all cached → gather resolves for free
        left, maximum, _f = client._rate_limiter.chrome_snapshot()
        if left >= len(misses) or (left >= 1 and left >= maximum):
            # Enough slots — or the window will never stretch further under
            # the household split (review finding F4). Sync once before the
            # spend if we actually waited.
            if waited:
                await _hive_run_sync(client, remaining)
            return
        ts = client._rate_limiter._timestamps
        wait = 0.1
        if ts:
            wait = max(0.05, ts[0] + client._rate_limiter._window - time.monotonic())
        if await _sleep_cancellable_async(wait):
            return  # cancelled
        waited = True


def _relay_spawn_bg(coro) -> None:
    """Track a fire-and-forget relay task (or drop it with no loop)."""
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()
        return
    _relay_bg_tasks.add(task)
    task.add_done_callback(_relay_bg_tasks.discard)


def _relay_contribute_bg(a_name: str, b_name: str, result) -> None:
    """Fire-and-forget: share a fresh neal.fun result with the hive."""
    if not _relay_active():
        return
    ka, kb = craft.pair_key(a_name, b_name)
    entry = (ka, kb, result.name, result.emoji or "")

    async def run():
        global _relay_contributed
        added = await asyncio.to_thread(relay_client.contribute, [entry])
        if added:
            _relay_contributed += added

    _relay_spawn_bg(run())


def _bounty_preempted() -> bool:
    """Any user activity preempts fleet work instantly."""
    return bool(
        _current_command
        or _current_ib_command
        or list(_command_queue)
        or list(_ib_command_queue)
        or _cooling()
        or not _relay_active()
    )


def _fleet_slot_available(client) -> bool:
    """True if a rate slot is free right now (won't block on acquire)."""
    left, _max, _frac = client._rate_limiter.chrome_snapshot()
    return left > 0


async def _serve_hive(client, storage) -> str:
    """One pull-and-serve pass, triggered by the beat's work bit.

    Every item — pair or review — is answered by a FRESH neal call, never
    from local cache: each contribution is an independent neal sighting (so
    it counts toward peer review) and a poisoned local entry can never be
    re-propagated into the hive. The relay refuses us whenever any session
    in our household is mid-run. Serving never blocks on a rate slot — it
    checks availability first and stops when the window drains. Runs inline
    in the beat loop; the lapse threshold tolerates the gap."""
    global _bounties_worked, _bounty_progress
    if _bounty_preempted():
        return "blocked"
    left, _max, _frac = client._rate_limiter.chrome_snapshot()
    if left <= 0:
        return "blocked"  # rate-limited — don't even claim work we can't do
    items = await asyncio.to_thread(
        relay_client.pull_work, craft.bounty_claim_limit(left)
    )
    if items is None:
        _relay_mark_unreachable()
        return "unreachable"
    _relay_apply_hive(client)
    if not items:
        return "empty"
    done = 0
    status = "worked"
    _bounty_progress = (0, len(items))
    _paint_queue_panel(force=True)
    try:
        for it in items:
            if _bounty_preempted() or not _fleet_slot_available(client):
                status = "blocked"  # stop before we'd block on a slot
                break
            a_name = it.get("first") or ""
            b_name = it.get("second") or ""
            if not a_name or not b_name:
                continue
            key = craft.pair_key(a_name, b_name)
            try:
                res = await client.pair(a_name, b_name, fleet=True)
            except NealRateLimited:
                _trip_cooldown()
                status = "blocked"
                break
            except RateLimitCancelled:
                status = "blocked"
                break
            except Exception:
                continue
            _pair_cache[key] = res
            if res.name is not None:
                _record_recipes_batch([(res.name, a_name, b_name)])
            entry = (key[0], key[1], res.name, res.emoji or "")
            added = await asyncio.to_thread(relay_client.contribute, [entry])
            if added is None:
                _relay_mark_unreachable()
                status = "unreachable"
                break
            done += 1
            _bounties_worked += 1
            _bounty_progress = (done, len(items))
            _paint_queue_panel(force=True)
    finally:
        _bounty_progress = None
        _paint_queue_panel(force=True)
    # A cycle that served nothing (e.g. a non-429 neal outage: every pair
    # raised and continued) must NOT report "worked" — otherwise the worker
    # skips its backoff and bursts the whole rate budget on failing requests
    # (review finding F1). Report "blocked" so it idle-polls instead.
    if status == "worked" and done == 0:
        status = "blocked"
    return status


async def _beat_worker(client, storage) -> None:
    """THE one timer (~1s): send a liveness beat, act on the work bit.

    The beat carries neal reachability, the active run id (a run's board
    entries live exactly as long as the id keeps appearing), and any 429
    cooldown to broadcast to the household. A failed beat marks the hive
    tier unreachable; the next successful one restores it — the beat IS
    the recovery probe, and it keeps flowing while the tier is down so a
    spun-down relay gets woken. Everything else in the hive protocol is
    an event."""
    global _relay_reachable
    while True:
        if _relay_user_on:
            cooled_ms = int(_cooldown_until * 1000) if _cooling() else 0
            resp = await asyncio.to_thread(
                relay_client.beat, not _cooling(), _run_id, cooled_ms
            )
            if resp is None:
                if _relay_reachable is not False:
                    _relay_mark_unreachable()
            else:
                if _relay_reachable is not True:
                    _relay_reachable = True
                _ok, work = resp
                if work and not _bounty_preempted():
                    with contextlib.suppress(Exception):
                        await _serve_hive(client, storage)
        await asyncio.sleep(craft.beat_interval_ms() / 1000.0)


def _request_skip_current() -> bool:
    """Skip the running queued command and continue to the next (Escape)."""
    global _cancelled, _discard_queue_after_cancel
    if (
        not _current_command
        and not _current_ib_command
        and not _waiting_for_confirm()
    ):
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


_main_task: asyncio.Task | None = None
_last_run_finished_at: float = 0.0


def _session_sigint():
    """Session-long SIGINT owner for the interactive REPL.

    The old design installed/removed a handler per command (cancel scope).
    CPython can defer Python-level signal processing while the loop thread
    is inside long C calls (curl transfers), so a Ctrl-C pressed mid-run
    could be PROCESSED after the scope exited — detonating as a raw
    KeyboardInterrupt that killed the REPL (stress-test round 4). One
    handler for the whole session means a delayed signal always lands
    here, never on default_int_handler.
    """
    if (
        _cancel_scope_depth > 0
        or _current_command
        or _current_ib_command
        or _command_queue
        or _ib_command_queue
    ):
        _on_sigint()
        return
    # Idle. A signal arriving just after a run finished is almost always a
    # stale mid-run Ctrl-C whose processing was deferred — ignore it
    # rather than exiting the REPL out from under the user.
    if time.monotonic() - _last_run_finished_at < 2.0:
        return
    # True idle Ctrl-C: exit like /quit via the ki_exit path.
    if _main_task is not None and not _main_task.done():
        _main_task.cancel()


def _enter_cancel_scope():
    """Mark one top-level queued command as cancel-scoped (depth only —
    the session-long _session_sigint handler owns SIGINT for the whole
    REPL lifetime, so there is no install/remove window for a deferred
    signal to fall through)."""
    global _cancel_scope_depth
    _cancel_scope_depth += 1


def _exit_cancel_scope():
    global _cancel_scope_depth, _last_run_finished_at
    _cancel_scope_depth -= 1
    if _cancel_scope_depth == 0:
        _last_run_finished_at = time.monotonic()


def _on_sigwinch():
    """Force chrome repaint on terminal resize (SIGWINCH). Best-effort, non-fatal."""
    if _chrome_enabled:
        with contextlib.suppress(Exception):
            _chrome_refresh(force=True)


def _install_winch_handler():
    """Install SIGWINCH handler for the interactive session (best effort, unix mostly)."""
    global _winch_previous
    if _winch_previous is not None or not hasattr(signal, "SIGWINCH"):
        return
    try:
        loop = asyncio.get_running_loop()
        _winch_previous = signal.getsignal(signal.SIGWINCH)
        loop.add_signal_handler(signal.SIGWINCH, _on_sigwinch)
    except (NotImplementedError, ValueError, RuntimeError):
        try:
            _winch_previous = signal.getsignal(signal.SIGWINCH)
            signal.signal(signal.SIGWINCH, lambda *_: _on_sigwinch())
        except (AttributeError, OSError, ValueError):
            _winch_previous = None


def _remove_winch_handler():
    """Restore previous SIGWINCH handler."""
    global _winch_previous
    if _winch_previous is None or not hasattr(signal, "SIGWINCH"):
        _winch_previous = None
        return
    try:
        loop = asyncio.get_running_loop()
        loop.remove_signal_handler(signal.SIGWINCH)
    except (NotImplementedError, ValueError, RuntimeError):
        try:
            signal.signal(signal.SIGWINCH, _winch_previous)
        except (AttributeError, OSError, ValueError):
            pass
    _winch_previous = None


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


def _is_zero_width(ch: str) -> bool:
    if not ch:
        return True
    if unicodedata.combining(ch):
        return True
    cat = unicodedata.category(ch)
    if cat.startswith("M") or cat == "Cf":
        return True
    return False


def _base_char_width(ch: str) -> int:
    if _is_zero_width(ch):
        return 0
    eaw = unicodedata.east_asian_width(ch)
    if eaw in ("F", "W"):
        return 2
    o = ord(ch)
    if 0x1F300 <= o <= 0x1F9FF or 0x1FA00 <= o <= 0x1FA6F or 0x2600 <= o <= 0x27BF:
        return 2
    if eaw == "A":
        return 2
    return 1


def _visible_cluster_advance(s: str, i: int) -> tuple[int, int]:
    """Grapheme cluster step for ZWJ/variant: (chw, new_i)."""
    n = len(s)
    if i >= n:
        return 0, i
    while i < n and _is_zero_width(s[i]):
        i += 1
    if i >= n:
        return 0, i
    ch = s[i]
    chw = _base_char_width(ch)
    i += 1
    saw_zwj = False
    while i < n:
        c = s[i]
        if _is_zero_width(c):
            if c == "\u200d":
                saw_zwj = True
            i += 1
            continue
        if saw_zwj:
            saw_zwj = False
            i += 1
            continue
        break
    return chw, i


def _ansi_visible_len(text: str) -> int:
    """Terminal column count after stripping ANSI SGR sequences.
    Supports combining/VS/ZWJ graphemes + emoji heuristic (no wcwidth dep)."""
    stripped = _ANSI_ESCAPE_RE.sub("", text)
    w = 0
    i = 0
    while i < len(stripped):
        chw, i = _visible_cluster_advance(stripped, i)
        w += chw
    return w


def _fit_visible(text: str, maxw: int) -> str:
    """Truncate to <= maxw visible cols; preserve whole ANSI SGRs and grapheme clusters (ZWJ/variant/emoji)."""
    if maxw <= 0 or not text:
        return ""
    if _ansi_visible_len(text) <= maxw:
        return text
    out_parts: list[str] = []
    vis = 0
    i = 0
    n = len(text)
    while i < n:
        m = _ANSI_ESCAPE_RE.match(text, i)
        if m:
            out_parts.append(m.group(0))
            i = m.end()
            continue
        # collect next cluster (to not split ZWJ etc) and decide by its width
        j = i
        cluster = ""
        chw = 0
        if j < n:
            if _is_zero_width(text[j]):
                cluster += text[j]
                j += 1
            else:
                ch0 = text[j]
                chw = _base_char_width(ch0)
                cluster += ch0
                j += 1
                saw_zwj = False
                while j < n:
                    c = text[j]
                    if _is_zero_width(c):
                        if c == "\u200d":
                            saw_zwj = True
                        cluster += c
                        j += 1
                        continue
                    if saw_zwj:
                        saw_zwj = False
                        cluster += c
                        j += 1
                        continue
                    break
        if vis + chw <= maxw:
            out_parts.append(cluster)
            vis += chw
        else:
            break
        i = j
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


def _chrome_update_scroll_region(
    *, reposition: bool = False, force: bool = False
) -> int:
    """Pin the bottom chrome; return the last line of the scrolling output region."""
    global _chrome_last_reserve, _chrome_last_height
    rows = _tty_height()
    reserve = _chrome_reserved_lines()
    bottom = max(1, rows - reserve)
    height_changed = rows != _chrome_last_height
    reserve_changed = reserve != _chrome_last_reserve
    if force or reserve_changed or height_changed:
        if reserve_changed and _chrome_last_reserve > reserve:
            # Rows that were chrome are now scrollable; clear stale queue/prompt text.
            clear_from = rows - _chrome_last_reserve + 1
            clear_to = rows - reserve
            for row in range(clear_from, clear_to + 1):
                _chrome_write_row(row)
        sys.stdout.write(f"\033[1;{bottom}r")
        sys.stdout.flush()
        _chrome_last_reserve = reserve
        _chrome_last_height = rows
        reposition = True
    if force or reposition:
        sys.stdout.write(f"\033[{bottom};1H")
        sys.stdout.flush()
    return bottom


def _chrome_active_prompt() -> str:
    """Prompt shown in the pinned chrome row (live while input is active)."""
    if (
        _waiting_for_confirm()
        or _bulk_confirm_pending
        or _confirm_expected
        or _chrome_input_active
    ):
        return _craft_prompt()
    return _chrome_prompt


def _chrome_state_key() -> tuple:
    # Include height/width so deltas force refresh/region (real size change is a change).
    return (
        _format_queue_display(),
        _chrome_active_prompt(),
        _chrome_input_active,
        _waiting_for_confirm(),
        _bulk_confirm_pending,
        _current_command,
        _current_ib_command,
        tuple(_command_queue),
        tuple(_ib_command_queue),
        _api_worker_task is not None and not _api_worker_task.done()
        if _api_worker_task
        else False,
        _ib_worker_task is not None and not _ib_worker_task.done()
        if _ib_worker_task
        else False,
        _tty_height(),
        _tty_width(),
    )


def _chrome_state_unchanged(force: bool) -> bool:
    if force:
        return False
    state = _chrome_state_key()
    if state == _chrome_last_state:
        return True
    return False


def _chrome_draw(*, partial: str = "", force: bool = False) -> None:
    """Draw queue panel and prompt on fixed rows below the scroll region.
    Throttled via _chrome_state_unchanged + force.
    """
    if not _chrome_enabled:
        return
    global _chrome_last_state
    if _chrome_state_unchanged(force):
        return
    state = _chrome_state_key()
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
    _chrome_last_state = state


def _chrome_refresh(*, force: bool = False, partial: str | None = None) -> None:
    """Repaint pinned chrome when queue state changes.
    Uses _chrome_state_unchanged + _chrome_state_key + _last_queue_snapshot + force for lightweight throttle:
    skips unnecessary updates/draws/region sets when identical.
    """
    global _last_queue_snapshot, _chrome_last_state
    if not _chrome_enabled:
        return
    if _chrome_state_unchanged(force):
        return
    state = _chrome_state_key()
    _chrome_last_state = state
    _last_queue_snapshot = _format_queue_display()
    _chrome_update_scroll_region(reposition=True, force=force)
    if partial is None and _chrome_input_active:
        partial = _chrome_partial
    _chrome_draw(partial=partial or "", force=True)


def _chrome_sync() -> None:
    """Refresh chrome after queue/confirm changes while the user may be mid-input.
    Uses force path (callers signal real change); throttled downstream via state_key.
    """
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
    _chrome_last_height = 0
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
    _chrome_last_height = 0
    _chrome_last_state = None


def _teardown_tty_and_chrome() -> None:
    """Centralized idempotent teardown for chrome + tty patches + winch + flags.
    Called from all exit paths (interactive finally, reset, harness, errors).
    """
    with contextlib.suppress(Exception):
        _remove_winch_handler()
    with contextlib.suppress(Exception):
        _patch_repl_print(False)
    with contextlib.suppress(Exception):
        _chrome_disable()
    global \
        _interactive_mode_active, \
        _confirm_expected, \
        _bulk_confirm_pending, \
        _bulk_confirm_resolved
    _interactive_mode_active = False
    _confirm_expected = False
    _bulk_confirm_pending = False
    _bulk_confirm_resolved = True


def _repl_print(*args, **kwargs):
    """Print into the scroll region without clobbering the pinned prompt."""
    file = kwargs.get("file", sys.stdout)
    if file is not sys.stdout or not _chrome_enabled:
        # Non-tty stdout is block-buffered; flush so piped/scripted runs see
        # progress as it happens (stress-test finding S3).
        kwargs.setdefault("flush", True)
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
        cols = _tty_width()
        safe = _fit_visible(text, max(0, cols - 1))
        sys.stdout.write(f"\033[{bottom};1H\033[K{safe}{end}")
        sys.stdout.write(
            RESET
        )  # close attrs if truncated mid-span (long name/emoji/FIRST)
        sys.stdout.flush()

        _chrome_draw(partial=partial, force=True)


def _repl_print_lines(text: str) -> None:
    """Print multi-line text into the scroll region without clobbering chrome."""
    if not text:
        return
    for line in text.split("\n"):
        _repl_print(line)


def _echo_submitted_command(line: str) -> None:
    """Echo the submitted command (dimmed) into the scroll region when chrome is active.

    With pinned chrome the input row is redrawn clean on submit and never emitted
    by the TTY reader. Echoing here ensures results have visible "what command
    produced this" context in the scrollback (the queue panel is transient).
    The echoed line is not indented so it stands out as context/header above
    the (indented) result output.
    """
    if line.strip() and _chrome_enabled:
        _repl_print_lines(_color(line, DIM))


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
    """Always treat bare [ or O as literal (prefer user data on CSI ambiguity).

    Prevents the orphan CSI handler from misclassifying documented search metachars
    (e.g. bare [, [A-Z]*, /foo[bar]/, fire*, mu?, !excl, ^first) as arrow/home/CSI.
    The ESC-lost recovery path is not used; full ESC sequences via
    _tty_collect_esc_sequence remain supported for arrows.
    Fast path, no poll delay (common [ / O in queries).
    """
    pending = [prefix] + _tty_slurp_stdin(stop_on_newline=False)
    extras = pending[1:]
    if extras:
        _tty_unread_stdin_many(extras)
    return None


def _tty_read_line() -> str:
    """Read a line in cbreak mode: arrows, history, and Escape to skip running work."""
    global _tty_stdin_unread
    if _tty_read_byte_hook is None:
        if termios is None or tty is None:
            raise RuntimeError("TTY required but termios/tty unavailable")
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
                # bare [O always literal (orphan fn returns None); slurp+unread extras for prod
                # fidelity (e.g. "[A-Z]*" etc never hijacked as CSI). Arrows/home only via ESC path.
                _tty_try_read_orphan_csi(ch)
                # fallthrough -> isprintable inserts the literal ch
            if ch.isprintable() or ch == "\t":
                # Instant single-key y/n when a bulk confirm is waiting
                # (no Enter). Other keys still build a normal line so
                # commands can be typed+Enter-queued during confirm.
                if (
                    not buf
                    and (_waiting_for_confirm() or _bulk_confirm_pending)
                    and craft.is_confirm_answer(ch)
                ):
                    if _chrome_enabled:
                        _chrome_update_scroll_region(reposition=True)
                        _chrome_draw(partial="")
                    else:
                        sys.stdout.write("\n")
                        sys.stdout.flush()
                    return craft.confirm_answer_key(ch)
                buf.insert(pos, ch)
                pos += 1
                history_index = None
                history_draft = ""
                _tty_refresh_input(buf, pos)
    finally:
        if use_real_tty and old is not None:
            with contextlib.suppress(Exception):
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


def _waiting_for_confirm() -> bool:
    return _confirm_future is not None and not _confirm_future.done()


def _route_confirm_input(line: str) -> bool:
    """Deliver y/n to active confirm or buffer until the worker is ready."""
    route = craft.confirm_input_route(
        line, _waiting_for_confirm(), bool(_bulk_confirm_pending)
    )
    if route == "deliver":
        key = craft.confirm_answer_key(line)
        if _confirm_future is not None and not _confirm_future.done():
            _confirm_future.set_result(key)
        return True
    if route == "buffer":
        global _confirm_answer_buffer
        _confirm_answer_buffer = craft.confirm_answer_key(line)
        return True
    return False


def _paint_job_chrome() -> None:
    """Repaint sticky chrome for job progress/pair (interactive chrome only).

    Non-interactive callers of _combine_pairs must not flood stdout with the
    permanent rate line on every pair.
    """
    if _chrome_enabled or _interactive_mode_active:
        _paint_queue_panel(force=True)


def _set_lane_progress(lane: str, done: int, total: int) -> None:
    """Set job progress for one chrome lane ('pair' or 'ib'); does not clobber the other."""
    global _job_done, _job_total, _ib_job_done, _ib_job_total
    if lane == "ib":
        _ib_job_done = done
        _ib_job_total = total
    else:
        _job_done = done
        _job_total = total
    _paint_job_chrome()


def _set_last_pair(a: str, b: str) -> None:
    global _last_pair
    _last_pair = (a, b)
    _paint_job_chrome()


def _clear_lane_progress(lane: str) -> None:
    """Zero one lane's progress; pair also clears _last_pair."""
    global _job_done, _job_total, _ib_job_done, _ib_job_total, _last_pair
    if lane == "ib":
        _ib_job_done = 0
        _ib_job_total = 0
    else:
        _job_done = 0
        _job_total = 0
        _last_pair = None
    _paint_job_chrome()


def _rate_bar_colored(
    remaining: int,
    maximum: int,
    frac_milli: int = 1000,
    fleet_used: int = 0,
    *,
    colored: bool = True,
    left_width: int = _RATE_BAR_LEFT,
    right_width: int = _RATE_BAR_RIGHT,
) -> str:
    """Segmented bar: left age + right capacity, hive-aware.

    Capacity half: cyan = remaining, gold ▒ = slots lent to hive bounties,
    dark ░ = own spend. colored=False gives plain glyphs for /queue status.
    ``frac_milli`` is thousandths progress [0, 1000] from chrome_snapshot.
    """
    left_part, cyan_part, gold_part, dark_part = craft.rate_bar_split_segments(
        remaining, fleet_used, maximum, frac_milli, left_width, right_width
    )
    if not colored:
        return left_part + cyan_part + gold_part + dark_part
    # MAGENTA reads as purple in most terminals; cyan matches the trainer;
    # YELLOW is the terminal's honey-gold.
    out = _color(left_part, MAGENTA) + _color(cyan_part, CYAN)
    if gold_part:
        out += _color(gold_part, YELLOW)
    return out + dark_part


def _awaiting_bulk_confirm_setup() -> bool:
    """True while a bulk command is starting but confirm UI is not ready yet."""
    return (
        bool(_current_command)
        and not _bulk_confirm_resolved
        and not _waiting_for_confirm()
    )


async def _await_confirmation(prompt: str) -> str:
    """Request confirmation via the interactive loop (avoids competing prompts)."""
    if not _interactive_mode_active:
        return await _prompt_input(prompt)
    global \
        _confirm_future, \
        _confirm_expected, \
        _confirm_answer_buffer, \
        _chrome_prompt, \
        _chrome_partial
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
                _chrome_prompt = _craft_prompt()
                # keep updated for post-output draws to restore correct (confirm or craft) prompt row
                _chrome_refresh(force=True)
        else:
            _chrome_sync()


async def _prompt_continue(
    warn_lines: str | list[str] | None = None,
    *,
    bulk_pending: bool = False,
    cancel_msg: str = "Cancelled.",
    reason: str = "",
) -> bool:
    """Print optional context, await y/n, return True to continue / False to cancel.

    Reason belongs on the job chrome next to the prompt. Keybindings live only
    on confirm [y/n]>. Owns cleanup of _bulk_confirm_pending / _confirm_expected
    / _confirm_answer_buffer / _confirm_reason.
    Policy is craft.confirm_should_continue; I/O is _await_confirmation.
    EOF/KeyboardInterrupt → cancel.
    """
    global _bulk_confirm_pending, _confirm_expected, _confirm_answer_buffer
    global _confirm_reason

    # Interactive mode can always prompt — with piped stdin the answer is
    # the next piped line, and EOF cancels. The historical isatty-only rule
    # for bulk confirms silently auto-approved piped /lucky, /permutate,
    # etc. (stress-test round 4, unbounded API burn). Truly non-interactive
    # runs (subcommands) still announce-and-proceed per spec §7.4.
    can_prompt = sys.stdin.isatty() or _interactive_mode_active

    _confirm_reason = reason
    if bulk_pending and can_prompt:
        _bulk_confirm_pending = True
        _chrome_sync()

    if isinstance(warn_lines, str) and warn_lines:
        _repl_print_lines(warn_lines)
    elif isinstance(warn_lines, list):
        for line in warn_lines:
            if line:
                _repl_print_lines(line)

    if not can_prompt:
        # No chrome/prompt — keep a one-line reason so non-TTY logs stay useful.
        if reason and not warn_lines:
            _repl_print_lines(f"  {reason}")
        return True

    try:
        try:
            answer = await _await_confirmation("")
        except (EOFError, KeyboardInterrupt):
            _repl_print_lines(f"  {cancel_msg}")
            _mark_cancel_notified()
            return False
        if not craft.confirm_should_continue(answer):
            _repl_print_lines(f"  {cancel_msg}")
            _mark_cancel_notified()
            return False
        return True
    finally:
        _bulk_confirm_pending = False
        _confirm_expected = False
        _confirm_answer_buffer = None
        _confirm_reason = ""
        if _chrome_enabled:
            _chrome_prompt = _craft_prompt()
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


async def _combine_pairs(client, storage, pairs: list[tuple], collect: dict | None = None):
    """Combine a list of (element, element) pairs with light parallelism.

    Pairs execute in kernel priority order — proven combiners first
    (ingredient-usage score descending, pair-key tie-break). This is the
    single choke point, so permute/cross/with/exhaust and each crawl
    generation all get the same ordering."""
    if len(pairs) > 1:
        # One hive sweep for the whole batch: anything any user has already
        # tried becomes a local cache hit before we spend a single neal slot,
        # and the cache-first prioritization below promotes it to the front.
        await _hive_sweep(client, pairs)
    if len(pairs) > 1:
        pair_elements = {e.name: e for pair in pairs for e in pair}
        pair_tuples = craft.prioritize_pairs_boundary(
            [
                (
                    a.name,
                    a.emoji or "",
                    bool(a.is_first_discovery),
                    b.name,
                    b.emoji or "",
                    bool(b.is_first_discovery),
                )
                for a, b in pairs
            ],
            _load_recipes(),
            [f"{ka}\0{kb}" for (ka, kb) in _pair_cache.keys()],
        )
        pairs = _pairs_from_boundary(pair_tuples, pair_elements.values())
    # Demand heartbeat: overflow beyond the live horizon is leased to the
    # bounty board and re-affirmed every sync interval — idle users elsewhere
    # work it into the shared cache while we grind our own share, and each
    # beat absorbs their results as free local hits. The run OWNS the task:
    # cancelling it (the finally below) is what lets a cancelled run's board
    # entries lapse, so it must stop on every exit path.
    total = len(pairs)
    new_count = 0
    nothing_count = 0
    done_count = 0
    started_count = 0
    known_names = {e.name for e in storage.get_all()}
    _set_lane_progress("pair", 0, total)

    async def process(a, b):
        nonlocal new_count, nothing_count, done_count, started_count
        _set_last_pair(a.name, b.name)
        # Show the STARTED ordinal: with concurrent fetches, done+1 lagged
        # far behind the real API spend (stress-test round 4 amplifier).
        started_count += 1
        _set_lane_progress("pair", min(total, started_count), total)
        try:
            result = await _cached_pair(client, storage, a, b, skip_relay_lookup=True)
        except CommandCancelled:
            return
        except NealRateLimited:
            # Cooldown message already printed by _trip_cooldown (or the
            # gate found an active cooldown); the batch loop stops the run.
            return
        except Exception as e:
            done_count += 1
            _set_lane_progress("pair", done_count, total)
            _repl_print_lines(
                f"  [{done_count}/{total}] {format_element(a)} + {format_element(b)} = "
                f"{_color(f'Error: {_tty(str(e))}', RED)}"
            )
            return
        done_count += 1
        _set_lane_progress("pair", done_count, total)
        if result.name is not None:
            for elem in (a, b):
                storage.add(
                    name=craft.sanitize_element_name(elem.name),
                    emoji=elem.emoji,
                    is_first_discovery=False,
                )
            storage.add(
                name=craft.sanitize_element_name(result.name),
                emoji=result.emoji,
                is_first_discovery=result.is_first_discovery,
            )
        result_display = result.name if result.name else "Nothing"
        _history.append((a.name, b.name, result_display))
        if result.name is None:
            nothing_count += 1
        else:
            tag = ""
            if collect is not None and result.name not in collect["_seen_products"]:
                collect["_seen_products"].add(result.name)
                collect["products"].append(
                    (result.name, result.emoji or "", bool(result.is_first_discovery))
                )
            if result.name not in known_names:
                tag = " " + _color("[NEW]", BOLD + GREEN)
                new_count += 1
                known_names.add(result.name)
                if collect is not None:
                    collect["news"].append(
                        (result.name, result.emoji or "", bool(result.is_first_discovery))
                    )
            hit = craft.is_target_hit(_target_element, result.name or "")
            if hit:
                tag += " " + _color("★ TARGET ★", BOLD + YELLOW + MAGENTA)
            _repl_print_lines(
                f"  [{done_count}/{total}] {format_element(a)} + {format_element(b)} = "
                f"{format_element(result)}{tag}"
            )
            if hit:
                stop = await _acknowledge_target_hit(a.name, b.name, result.name or "")
                if stop:
                    return

    # The run asserts an id from here on: every beat carries it, and the
    # run's board entries live exactly as long as it keeps appearing. The
    # finally clears it — that IS cancellation, on every exit path.
    global _run_id, _run_seq, _run_posted_once
    _run_seq += 1
    _run_posted_once = False
    # Process in batches of API_CONCURRENCY to avoid overwhelming the rate limiter
    try:
        _run_id = f"{relay_client.SESSION_ID}-{_run_seq}"
        if _relay_active() and len(pairs) > 1:
            await _hive_run_sync(client, pairs)
        for i in range(0, len(pairs), API_CONCURRENCY):
            if _cancelled:
                break
            if _cooling():
                skipped = total - done_count
                _repl_print_lines(
                    f"  {_color(f'429 cooldown — {skipped} pairs skipped.', RED)}"
                )
                break
            batch = pairs[i : i + API_CONCURRENCY]
            # Event-driven rate wait + sync-before-spend: sleep until slots
            # free (no ticks), then absorb hive fills so a scarce neal slot
            # is spent only on pairs the fleet genuinely hasn't provided.
            if _relay_active():
                await _hive_wait_for_slots(client, batch, pairs[i:])
                if _cancelled:
                    break
                if _cooling():
                    skipped = total - done_count
                    _repl_print_lines(
                        f"  {_color(f'429 cooldown — {skipped} pairs skipped.', RED)}"
                    )
                    break
            # Show the first pair of each batch immediately (before the fetch).
            if batch:
                _set_last_pair(batch[0][0].name, batch[0][1].name)
            await asyncio.gather(*(process(a, b) for a, b in batch))
    finally:
        # Dropping the run id from our beats is what expires this run's
        # board entries — the relay clears them beat_lapse_ms later, on
        # every exit path, with no revoke call.
        _run_id = ""

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
    global _bulk_confirm_resolved
    if craft.bulk_confirm_required(len(pairs), _BULK_WARN_THRESHOLD, _auto_approve):
        if not await _prompt_continue(
            bulk_pending=True,
            reason=f"{len(pairs)} pairs",
        ):
            return
        _bulk_confirm_resolved = True
    else:
        if _auto_approve and craft.should_bulk_warn(len(pairs), _BULK_WARN_THRESHOLD):
            _repl_print_lines(
                _color(f"  Auto-approved {len(pairs)} pairs (/auto is on).", DIM)
            )
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
        _repl_print_lines(
            f"  Only one match: {format_element(matches[0])}. Need at least two."
        )
        return

    n = len(matches)
    pairs = _pairs_from_boundary(
        craft.permute_pairs_boundary(_elements_to_boundary(matches)), matches
    )
    _repl_print_lines(
        f"  {n} element{'' if n == 1 else 's'} match, "
        f"{len(pairs)} unique pair{'' if len(pairs) == 1 else 's'}:"
    )
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
            f"  Permuting matches for {_color(query, YELLOW)} until no new discoveries..."
        )
        _repl_print_lines("  (Ctrl+C to stop)")

        while True:
            if _cancelled:
                stopped = True
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
            pairs = _pairs_from_boundary(
                craft.permute_pairs_boundary(_elements_to_boundary(matches)), matches
            )
            _repl_print_lines(
                f"  --- Round {round_num}: {n} elements, {len(pairs)} pairs ---"
            )

            if not confirmed and craft.bulk_confirm_required(
                len(pairs), _BULK_WARN_THRESHOLD, _auto_approve
            ):
                if not await _prompt_continue(
                    bulk_pending=True,
                    reason=f"{len(pairs)} pairs per round",
                ):
                    return
                confirmed = True
                _bulk_confirm_resolved = True
            elif not confirmed:
                if _auto_approve and craft.should_bulk_warn(
                    len(pairs), _BULK_WARN_THRESHOLD
                ):
                    _repl_print_lines(
                        _color(
                            f"  Auto-approved {len(pairs)} pairs per round (/auto is on).",
                            DIM,
                        )
                    )
                    confirmed = True
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
                _repl_print_lines("  Stopped early.")
                _mark_cancel_notified()
        else:
            _repl_print_lines(
                f"  Permutate done after {round_num} round{'' if round_num == 1 else 's'}."
            )
    finally:
        _confirm_expected = False
        _confirm_answer_buffer = None
        if _chrome_enabled:
            _chrome_prompt = _craft_prompt()


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

    pairs = _pairs_from_boundary(
        craft.cross_pairs_boundary(
            _elements_to_boundary(left), _elements_to_boundary(right)
        ),
        left + right,
    )

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
    _repl_print_lines(f"  {len(pairs)} unique pair{'' if len(pairs) == 1 else 's'}")
    await _confirm_and_run_pairs(client, storage, pairs)


# ---------------------------------------------------------------------------
# Infinibrowser integration
# ---------------------------------------------------------------------------
_IB_BASE = "https://infinibrowser.wiki/api"


def _ib_fetch(
    path: str, params: dict, use_cache: bool = True, quiet: bool = False
) -> dict | None:
    """Fetch from the Infinibrowser API. Prints errors on failure unless quiet."""
    result = fetch_json(f"{_IB_BASE}/{path}", params=params, use_cache=use_cache)
    if result is None and not quiet:
        _repl_print_lines(f"  {_color('Infinibrowser request failed', RED)}")
    return result



def _lineage_step_tuples(steps) -> list[tuple[str, str, str, str, str, str]]:
    """Tolerant JSON extraction of Infinibrowser lineage steps into the
    kernel's (a, a_emoji, b, b_emoji, result, result_emoji) tuples. Falls
    back from id to text and passes empty strings for missing fields — the
    kernel skips malformed steps."""

    def _part(step, key):
        part = step.get(key) or {}
        return (
            str(part.get("id") or part.get("text") or ""),
            part.get("emoji") or "",
        )

    tuples = []
    for step in steps:
        a_name, a_emoji = _part(step, "a")
        b_name, b_emoji = _part(step, "b")
        r_name, r_emoji = _part(step, "result")
        tuples.append((a_name, a_emoji, b_name, b_emoji, r_name, r_emoji))
    return tuples


async def _import_from_infinibrowser_async(storage, name: str) -> str:
    """Look up an element on Infinibrowser, show its lineage, and import into discoveries."""
    data = await asyncio.to_thread(_ib_fetch, "item", {"id": name})
    if data is None:
        return ""
    if "code" in data:
        return f"  {_color('Not found', DIM)} on Infinibrowser: {format_element(Element(name=name))}"

    emoji = data.get("emoji", "")
    depth = data.get("depth", "?")
    item_name = craft.sanitize_element_name(data["text"])
    found_elem = Element(name=item_name, emoji=emoji or None, is_first_discovery=None)
    _repl_print_lines(f"  Found: {format_element(found_elem)}  (depth {depth})")

    lineage = await asyncio.to_thread(_ib_fetch, "recipe", {"id": name}, False)
    if lineage is None:
        return ""
    steps = lineage.get("steps", [])
    if not steps:
        return f"  No lineage available for {format_element(Element(name=name))}."

    _repl_print_lines(f"  Lineage ({len(steps)} steps):")
    import_batch, recipe_batch = craft.lineage_steps_to_batches(
        _lineage_step_tuples(steps)
    )
    emoji_by_name = {n: em for n, em, _f in import_batch}
    for r_name, a_name, b_name in recipe_batch:
        a_elem = Element(name=a_name, emoji=emoji_by_name.get(a_name) or None)
        b_elem = Element(name=b_name, emoji=emoji_by_name.get(b_name) or None)
        r_elem = Element(name=r_name, emoji=emoji_by_name.get(r_name) or None)
        _repl_print_lines(
            f"    {format_element(a_elem)} + {format_element(b_elem)} = {format_element(r_elem)}"
        )
    _record_recipes_batch(recipe_batch)
    storage.add_batch(import_batch)

    storage.reload()
    return f"  Imported {_color(str(len(import_batch)), GREEN)} elements into discoveries."


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

    item_tuples = [
        (
            item["id"],
            str(item.get("text", "")),
            item.get("emoji", ""),
            bool(item.get("discovery") or item.get("discovered")),
        )
        for item in items
    ]
    recipe_refs = []
    for item in items:
        for recipe in item.get("recipes", []):
            if len(recipe) == 2:
                recipe_refs.append((item["id"], recipe[0], recipe[1]))
    element_batch, recipe_batch = craft.ic_save_to_batches(item_tuples, recipe_refs)
    imported_count = storage.add_batch(element_batch)
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


async def _fill_missing_recipes_async(storage):
    """Fetch lineages from Infinibrowser for elements missing recipes.

    When a lineage is fetched, its intermediate elements get recipes too,
    so we re-check the missing set after each fetch to skip already-filled items.
    """
    recipes = _load_recipes()
    missing = set(
        craft.unfilled_names_boundary(
            _elements_to_boundary(storage.get_all()), recipes
        )
    )
    if not missing:
        _repl_print_lines("  All elements have recipes.")
        return

    total = len(missing)
    _repl_print_lines(
        f"  {total} element{'' if total == 1 else 's'} missing recipes. Fetching from Infinibrowser..."
    )
    _repl_print_lines("  (Ctrl+C to stop early)")
    fetched = 0
    skipped = 0
    failed = set()
    processed = 0
    queue = sorted(missing)
    _set_lane_progress("ib", 0, total)
    try:
        for name in queue:
            if _cancelled:
                _repl_print_lines("  Stopped early.")
                _mark_cancel_notified()
                break
            # Track filled-ness via the local `missing` set (updated from each
            # lineage's result names). Avoid craft.is_unfilled in this loop —
            # it deep-copies the whole recipe map per call (sudoc Map arg
            # semantics) and was multi-second on large saves.
            if name not in missing or name in failed:
                skipped += 1
                continue
            processed += 1
            _set_lane_progress("ib", processed, total)
            remaining = total - fetched - skipped - len(failed)
            e = storage.get_by_name(name) or Element(name=name)
            _repl_print_lines(
                f"  [{processed}/{total}] {format_element(e)} ({remaining} remaining)..."
            )
            data = await asyncio.to_thread(
                _ib_fetch, "item", {"id": name}, quiet=True
            )
            if data is None or "code" in data:
                failed.add(name)
                continue
            lineage = await asyncio.to_thread(
                _ib_fetch, "recipe", {"id": name}, quiet=True
            )

            if lineage is None:
                failed.add(name)
                continue
            element_batch, recipe_batch = craft.lineage_steps_to_batches(
                _lineage_step_tuples(lineage.get("steps", []))
            )
            _record_recipes_batch(recipe_batch)
            storage.add_batch(element_batch)
            # Results in this lineage now have at least one recipe pair.
            for result_name, _a, _b in recipe_batch:
                missing.discard(result_name)
            fetched += 1
            if await _sleep_cancellable_async(0.5):
                _repl_print_lines("  Stopped early.")
                _mark_cancel_notified()
                break
    except KeyboardInterrupt:
        _repl_print_lines("  Stopped early.")
    _mark_cancel_notified()
    storage.reload()
    summary = (
        f"  Fetched {fetched} lineages, {skipped} already filled by prior lineages."
    )
    if failed:
        summary += f" {_color(str(len(failed)), YELLOW)} not found on Infinibrowser."
    _repl_print_lines(summary)


def do_unfilled(storage) -> str:
    """List elements that have no recipes (excluding base elements)."""
    recipes = _load_recipes()
    discoveries = storage.get_all()
    missing_names = set(
        craft.unfilled_names_boundary(_elements_to_boundary(discoveries), recipes)
    )
    missing = [e for e in discoveries if e.name in missing_names]
    if not missing:
        return "  All elements have recipes."
    lines = [f"  {len(missing)} element{'' if len(missing) == 1 else 's'} without recipes:"]
    for e in missing:
        lines.append(f"    {format_element(e)}")
    return "\n".join(lines)


def _orphan_candidates(storage) -> list:
    """Discoveries with no recipe lineage and not referenced as a constituent."""
    orphan_names = {
        name
        for name, _emoji, _first in craft.orphan_candidates_boundary(
            _elements_to_boundary(storage.get_all()), _load_recipes()
        )
    }
    return [e for e in storage.get_all() if e.name in orphan_names]


def _ib_can_fill(name: str) -> bool | None:
    """Whether /fill could fetch a recipe for name.

    Returns True if fillable, False if Infinibrowser has no recipe, None on API error.
    """
    try:
        item_resp = ib_get(f"{_IB_BASE}/item", params={"id": name})
        if item_resp is None:
            return None
        if item_resp.status_code == 404:
            return False
        if not item_resp.ok:
            return None
        item_data = item_resp.json()
        if "code" in item_data:
            return False

        recipe_resp = ib_get(f"{_IB_BASE}/recipe", params={"id": name})
        if recipe_resp is None:
            return None
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
            _repl_print_lines(f"  [{i}/{total}] {format_element(elem)}...")
            fillable = await asyncio.to_thread(_ib_can_fill, elem.name)
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
    summary = (
        f"  Pruned {_color(str(pruned), GREEN)} element{'s' if pruned != 1 else ''}."
    )
    if kept:
        summary += f" {kept} fillable on Infinibrowser (kept)."
    if skipped:
        summary += f" {_color(str(skipped), YELLOW)} skipped (API errors)."
    _repl_print_lines(summary)


def do_export(storage, path: str = EXPORT_PATH) -> str:
    """Export discoveries to an Infinite Craft .ic save file.

    Includes elements that have recipes, are base elements, or are referenced
    as constituents by any included recipe (e.g. terminal leaves from /fill
    or /import lineages). This ensures filled recipes survive export/import.
    Pure orphans with no recipes and not referenced by any recipe are excluded.
    """
    recipes = _load_recipes()
    discoveries = storage.get_all()
    item_tuples, recipe_refs = craft.build_export_items_boundary(
        _elements_to_boundary(discoveries), recipes
    )
    items = []
    for item_id, name, emoji, is_first in item_tuples:
        item = {"id": item_id, "text": name, "emoji": emoji or ""}
        if is_first:
            item["discovery"] = True
        items.append(item)
    for result_id, a_id, b_id in recipe_refs:
        items[result_id].setdefault("recipes", []).append([a_id, b_id])

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
    <element> + <element>         Combine two elements
    /combine <element> <element>  Combine two elements

  Crawl:
    <element> ++ <element>        Combine & crawl until no new discoveries
    /crawl <element> <element>    Combine & crawl until no new discoveries

  Bulk combine (query syntax below):
    /with <element> <query>       Combine element with all matching discoveries
    <query> * <query>             Cross-combine matches from both queries
    /cross <query> <query>        Cross-combine matches from both queries
    /permute <query>              Combine all matching elements with each other
    /permutate <query>            Permute repeatedly until no new discoveries
    /exhaust <query>              Each match combined with all discoveries
    /lucky [count]                Try random untried pairs (default 10)

  Target:
    /target <element>             Ask y/n to continue the batch when this is crafted
    /target                       Show current target
    /target clear                 Clear target
    /auto [on|off]                Auto-approve bulk y/n confirms (bare /auto toggles)

  Hive mind:
    /relay [on|off|status]        Shared pair cache with other users (bare /relay
                                  toggles; on by default). Pairs anyone has tried
                                  are served from the hive without spending your
                                  rate limit; your fresh results are shared back.

  Query syntax (/search, /with, /permute, /permutate, /cross, /exhaust, shorthands):
    substring                     Default: case-insensitive substring
    * ? []                        fnmatch wildcards (e.g. fire*, mu?)
    /pattern/                     Regex, case-insensitive (| alternation, \\d escapes)
    !<query>                      Exclude matches (e.g. !fire* = everything except fire*)
    !                             All elements (exclude nothing)
    ^<query>                      First discoveries only (e.g. ^fire* = new fire* matches)
    ^                             All first discoveries

  Scripting (every non-slash line is a script):
    stmt ; stmt                   Run statements in sequence
    name := expr                  Bind a set for this script run
    a* , b*                       Union    a* - b*  difference    a* & b*  intersect
    a* / b*                       Keep a* having a known recipe with b* (% = lacking)
    (expr)*  (expr)**  (expr)!    Permute / permutate / exhaust the set
    (expr)100  (expr)100?  (expr)?  First 100 / random 100 / shuffle ((expr)(|x*|) = dynamic)
    [ expr ]  /  []               New elements made by expr / by the last operation
    ^(expr)                       First discoveries only
    set @ body   set @x body      For each element (as _ or x) run body
    body -> cond                  Run body, repeat until cond is true
    body ~ cond                   While cond is true, run body
    cond ? body : body            Conds: |expr| sizes, comparisons, && ||
    "exact name"                  Quoted = exact element (spaces, commas, shadows)
    /script <path.ice>            Run a saved script file

  Discoveries & recipes:
    /search <query>               Search discoveries
    /recipe <element>             Show shortest recipe from base elements
    /list                         List all discovered elements
    /import <element|file.ic>     Import from Infinibrowser or .ic save file
    /fill                         Fetch missing recipes from Infinibrowser
    /unfilled                     List elements without recipes
    /prune                        Remove orphan elements Infinibrowser can't fill
    /export [path]                Export discoveries as .ic save file
    /history                      Show combinations tried this session
    /clear                        Clear output (browser only)
    /queue                        Show running and pending commands
                                  (also shown above the prompt)
    /help                         Show this help
    /quit                         Exit

  Background queue (long API commands):
    Esc                           Skip current command, continue to next in queue
                                  (TTY only; skips during rate-limit/backoff waits
                                  (⏳ rate limit shows in queue panel),
                                  not during an active network request; bulk
                                  commands may finish in-flight pairs first)
    Ctrl+C                        While running: stop and discard remaining queue
                                  At bulk confirm [y/n]: decline only (queue kept)"""


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

def do_target(arg: str) -> str:
    """Set, clear, or show the combination target element (rules in kernel)."""
    global _target_element
    action, name = craft.parse_target_arg(arg)
    kind, new_state, detail = craft.target_outcome(_target_element, action, name)
    if kind == "show_empty":
        return f"  No target set. Usage: {_color('/target <element>', YELLOW)}"
    if kind == "show":
        return f"  Target: {_color(new_state, BOLD + YELLOW)}"
    # Mutating kinds: assign session target, then host-format message.
    _target_element = new_state
    if kind == "clear_empty":
        return "  No target was set."
    if kind == "clear":
        return f"  Target cleared (was {_color(detail, YELLOW)})."
    return (
        f"  Target set: {_color(new_state, BOLD + YELLOW)} — "
        "you'll be asked whether to continue the batch when this is crafted."
    )


def do_auto(arg: str) -> str:
    """Toggle, set, or show session auto-approve (rules in kernel)."""
    global _auto_approve
    kind, new_state = craft.auto_approve_outcome(_auto_approve, arg)
    if kind == "invalid":
        return f"  Usage: {_color('/auto [on|off]', YELLOW)} (bare /auto toggles)"
    _auto_approve = new_state
    if kind == "on":
        return (
            f"  Auto-approve {_color('on', GREEN)} — bulk y/n confirms are "
            "skipped. Target hits still ask."
        )
    if kind == "off":
        return (
            f"  Auto-approve {_color('off', YELLOW)} — runs over "
            f"{_BULK_WARN_THRESHOLD} pairs ask y/n."
        )
    if kind == "show_on":
        return f"  Auto-approve is {_color('on', GREEN)}."
    return f"  Auto-approve is {_color('off', YELLOW)}."


def do_relay(arg: str, storage) -> str:
    """Toggle, set, or show the hive-mind relay tier (grammar in kernel)."""
    global _relay_user_on
    kind, new_state = craft.relay_toggle_outcome(_relay_user_on, arg)
    if kind == "invalid":
        return f"  Usage: {_color('/relay [on|off|status]', YELLOW)} (bare /relay toggles)"
    _relay_user_on = new_state
    if new_state and _relay_reachable is not True:
        _relay_spawn_warmup(storage)
    if not new_state:
        # Turning the tier off drops arbitration — restore the full budget.
        _relay_restore_budget()
    if _relay_reachable is True:
        conn = _color("connected", GREEN)
    elif _relay_reachable is None:
        conn = _color("warming up", YELLOW)
    else:
        conn = _color("unreachable", RED)
    counters = (
        f"{_color(str(_relay_hits), GREEN)} served from hive, "
        f"{_relay_contributed} contributed, "
        f"{_color(str(_bounties_worked), YELLOW)} bounties worked"
    )
    peers = int(relay_client.last_hive.get("peers") or 0)
    extras = []
    if peers > 1:
        extras.append(
            f"{peers} sessions spending on this IP — budget split to "
            f"{craft.effective_rate_limit(API_RATE_LIMIT, peers)}/min each"
        )
    if _cooling():
        resume = time.strftime("%H:%M", time.localtime(_cooldown_until))
        extras.append(_color(f"429 cooldown until ~{resume}", RED))
    extra_line = ("\n  " + "\n  ".join(extras)) if extras else ""
    if kind == "on":
        return f"  Relay {_color('on', GREEN)} ({conn}) — {counters}.{extra_line}"
    if kind == "off":
        return f"  Relay {_color('off', YELLOW)} — pairs go straight to neal.fun."
    if kind == "show_on":
        return (
            f"  Relay is {_color('on', GREEN)} ({conn}) — {counters}.{extra_line}\n"
            f"  {_color(relay_client.relay_url(), DIM)}"
        )
    return f"  Relay is {_color('off', YELLOW)}."


async def _acknowledge_target_hit(a_name: str, b_name: str, result_name: str) -> bool:
    """Pause for confirm after a target hit. Returns True if the batch should stop.

    y continues; n / empty / Esc cancels remaining work (sets _cancelled).
    """
    global _cancelled
    async with _target_hit_lock:
        if _cancelled:
            return True
        _repl_print_lines(
            f"  {_color('★ TARGET HIT ★', BOLD + YELLOW)} "
            f"{_tty(a_name)} + {_tty(b_name)} = {_color(_tty(result_name), BOLD + YELLOW + MAGENTA)}"
        )
        if not await _prompt_continue(
            cancel_msg="Stopped after target hit.",
            reason="target hit",
        ):
            _cancelled = True
            return True
        _repl_print_lines(f"  {_color('Continuing…', DIM)}")
        return False


def _validate_command_line(line: str) -> str | None:
    """Parse and validate a command before enqueue. Returns error text or None if OK.

    The full decision tree (and every message) lives in the kernel; this
    just renders the returned segments with ANSI color."""
    segments = craft.validate_command_line_segments(line)
    if segments is None:
        return None
    return _render_error_segments(segments)


def do_queue_status() -> str:
    """Describe the current command queue (pair + IB lanes)."""
    if _active_client is None:
        left, maximum, frac_milli, fleet_used = (60, 60, 1000, 0)
    else:
        left, maximum, frac_milli, fleet_used = (
            _active_client._rate_limiter.chrome_snapshot_split()
        )
    rate_line = (
        f"  rate {_rate_bar_colored(left, maximum, frac_milli, fleet_used, colored=False)}"
        f" {left}/{maximum}"
    )
    pair_q = list(_command_queue)
    ib_q = list(_ib_command_queue)
    if (
        not _current_command
        and not _current_ib_command
        and not pair_q
        and not ib_q
    ):
        return (
            f"{rate_line}\n"
            "  Queue is idle.\n"
            "  When you start a long command (combine, fill, permutate, ...), "
            "its status appears in the panel above the prompt."
        )
    lines: list[str] = [rate_line]
    if _current_command:
        line = f"  Running: {_sanitize_queue_line(_current_command)}"
        if _job_total > 0:
            line += f"  {_job_done}/{_job_total}"
        if _last_pair:
            line += f"  ·  {_last_pair[0]} + {_last_pair[1]}"
        lines.append(line)
    if _current_ib_command:
        line = f"  Running: {_sanitize_queue_line(_current_ib_command)}"
        if _ib_job_total > 0:
            line += f"  {_ib_job_done}/{_ib_job_total}"
        lines.append(line)
    n = 1
    for cmd in pair_q:
        lines.append(f"  {n}. pending: {_sanitize_queue_line(cmd)}")
        n += 1
    for cmd in ib_q:
        lines.append(f"  {n}. pending: {_sanitize_queue_line(cmd)}")
        n += 1
    return "\n".join(lines)


def _format_queue_display() -> str:
    """Render sticky chrome: always-on rate line, job line, pending queue.

    Rate line: remaining pair-API budget bar + last pair (while a job runs).
    Job line: running command + done/total (or confirm state).
    Queue: pending commands only (height/cap concerns apply here).
    """
    width = _tty_width()
    content: list[str] = []

    # --- Permanent rate line (always) ---
    if _active_client is None:
        left, maximum, frac_milli, fleet_used = (60, 60, 1000, 0)
    else:
        left, maximum, frac_milli, fleet_used = (
            _active_client._rate_limiter.chrome_snapshot_split()
        )
    bar = _rate_bar_colored(left, maximum, frac_milli, fleet_used)
    rate_prefix = f"  {_color('rate', DIM)} {bar} {left}/{maximum}"
    if _relay_hits > 0:
        rate_prefix += f" {_color('·', DIM)} {_color(f'🐝 +{_relay_hits}', YELLOW)}"
    if _bounty_progress is not None:
        rate_prefix += f" {_color('·', DIM)} {_color('🐝 serving', YELLOW)}"
    note = craft.rate_status_note(left)
    if note:
        rate_prefix += f" {_color('·', DIM)} {_color(note, YELLOW)}"
    if _cooling():
        resume = time.strftime("%H:%M", time.localtime(_cooldown_until))
        rate_prefix += f" {_color('·', DIM)} {_color(f'429 cooldown ~{resume}', RED)}"
    pair_part = ""
    if _current_command and _last_pair is not None:
        a, b = _last_pair
        pvis = _ansi_visible_len(rate_prefix) + 3  # " · "
        avail = max(8, width - pvis - 1)
        pair_part = (
            f" {_color('·', DIM)} {_sanitize_queue_line(craft.rate_format_pair_for_width(a, b, avail))}"
        )
    content.append(rate_prefix + pair_part)

    # --- Hive line (serving bounties while idle) ---
    if _bounty_progress is not None:
        bk, bn = _bounty_progress
        content.append(
            f"  {_color('🐝', YELLOW)} {_color('hive', DIM)}     "
            f"{_color(f'fulfilling bounties [{bk}/{bn}]', YELLOW)} "
            f"{_color('any input pauses instantly', DIM)}"
        )

    # --- Job lines (pair and/or IB may run concurrently) ---
    running = _current_command
    running_ib = _current_ib_command
    pending = list(_command_queue) + list(_ib_command_queue)
    if _waiting_for_confirm() or _bulk_confirm_pending:
        prefix = f"  {_color('◆', YELLOW)} {_color('confirm', BOLD + YELLOW)}  "
        cmd = _sanitize_queue_line(running or "")
        reason = _sanitize_queue_line(_confirm_reason) if _confirm_reason else ""
        reason_part = f" {_color('·', DIM)} {reason}" if reason else ""
        pvis = _ansi_visible_len(prefix) + _ansi_visible_len(reason_part)
        avail = max(1, width - pvis - 1)
        if _ansi_visible_len(cmd) > avail:
            cmd = _fit_visible(cmd, max(0, avail - 1)) + "…"
        content.append(f"{prefix}{_color(cmd, YELLOW)}{reason_part}")
    elif running:
        prefix = f"  {_color('▶', YELLOW)} {_color('running', DIM)}  "
        cmd = _sanitize_queue_line(running)
        prog = f" {_color(f'{_job_done}/{_job_total}', CYAN)}" if _job_total > 0 else ""
        pvis = _ansi_visible_len(prefix) + _ansi_visible_len(prog)
        avail = max(1, width - pvis - 1)
        if _ansi_visible_len(cmd) > avail:
            cmd = _fit_visible(cmd, max(0, avail - 1)) + "…"
        content.append(f"{prefix}{_color(cmd, YELLOW)}{prog}")
    if running_ib:
        # Same "running" chrome as pair; both lanes can show at once.
        prefix = f"  {_color('▶', YELLOW)} {_color('running', DIM)}  "
        cmd = _sanitize_queue_line(running_ib)
        prog = (
            f" {_color(f'{_ib_job_done}/{_ib_job_total}', CYAN)}"
            if _ib_job_total > 0
            else ""
        )
        pvis = _ansi_visible_len(prefix) + _ansi_visible_len(prog)
        avail = max(1, width - pvis - 1)
        if _ansi_visible_len(cmd) > avail:
            cmd = _fit_visible(cmd, max(0, avail - 1)) + "…"
        content.append(f"{prefix}{_color(cmd, YELLOW)}{prog}")

    # --- Pending queue (pair + ib interlaced for display) ---
    for i, cmd in enumerate(pending, 1):
        safe = _sanitize_queue_line(cmd)
        prefix = f"  {_color(f'{i}.', DIM)} {_color('pending', DIM)}  "
        pvis = _ansi_visible_len(prefix)
        avail = max(1, width - pvis - 1)
        if _ansi_visible_len(safe) > avail:
            safe = _fit_visible(safe, max(0, avail - 1)) + "…"
        content.append(f"{prefix}{safe}")

    # Compact (no decorative rules) when the pending queue is empty: rate-only
    # idle, or rate + job/confirm. Rules only wrap a multi-item pending list.
    if not pending:
        return "\n".join(content)
    overhead = 2 + 1 + 5 + 1
    sep_len = max(3, (width - overhead) // 2)
    sep_len = min(sep_len, 40)
    rule = _color("─" * sep_len, DIM)
    lines: list[str] = [f"  {rule} {_color('status', BOLD + CYAN)} {rule}"]
    if _ansi_visible_len(lines[0]) > width:
        sep_len = max(1, sep_len - 2)
        rule = _color("─" * sep_len, DIM)
        lines[0] = f"  {rule} {_color('status', BOLD + CYAN)} {rule}"
    lines.extend(content)
    header_vis = _ansi_visible_len(lines[0])
    foot_bar = "─" * max(3, header_vis - 2)
    foot = f"  {_color(foot_bar, DIM)}"
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
    """Redraw the queue panel above the prompt; clear it when idle.
    Lightweight throttle for chrome path using _chrome_state_unchanged / state_key / snapshot + force.
    """
    global _last_queue_snapshot, _queue_panel_height
    if _chrome_enabled:
        if not force:
            # early throttle using _chrome_state_unchanged (covers queue/prompt/confirm/height/width deltas etc)
            if _chrome_state_unchanged(force):
                return
        with _repl_print_lock:
            _chrome_refresh(force=force)
        return
    display = _format_queue_display()
    if display == _last_queue_snapshot and not force:
        return
    with _repl_print_lock:
        _erase_queue_panel()
        if display:
            print(display, flush=True)
            _queue_panel_height = (
                display.count("\n") + 1
            )  # auto 1 for compact single; 3+ for multi
    _last_queue_snapshot = display


def _craft_prompt() -> str:
    """Prompt string; hints when background work is active."""
    if _waiting_for_confirm() or _bulk_confirm_pending or _confirm_expected:
        return _color(CONFIRM_PROMPT, YELLOW)
    base = _color(CRAFT_PROMPT, CYAN)
    pair_q = list(_command_queue)
    ib_q = list(_ib_command_queue)
    if not (
        _current_command
        or _current_ib_command
        or pair_q
        or ib_q
    ):
        if _target_element:
            return base + _color(f"[target:{_target_element}] ", DIM)
        return base
    pending = (
        len(pair_q)
        + len(ib_q)
        + (1 if _current_command else 0)
        + (1 if _current_ib_command else 0)
    )
    hint = _color(f"[{pending} active] ", DIM)
    if _target_element:
        hint += _color(f"[target:{_target_element}] ", DIM)
    if (_current_command or _current_ib_command) and _tty_input_available():
        hint += _color("[Esc skip] ", DIM)
    return base + hint



# ---------------------------------------------------------------------------
# Script driver (spec v0.6)
# The kernel owns parse, static checks, and pure evaluation; this driver
# walks mutating spines and control flow, performing effects through the
# existing machinery. Sets are tuples (name, emoji, first).
# ---------------------------------------------------------------------------


class ScriptError(Exception):
    pass


class _ScriptState:
    __slots__ = ("nodes", "kids", "muts", "frames", "collectors", "loop_depth", "seed_base", "seed_tick")

    def __init__(self, nodes, kids, muts, seed_base=None):
        self.nodes = nodes
        self.kids = kids
        self.muts = muts
        self.frames: list[list] = [[]]  # scope frames of (name, set)
        self.collectors: list[list] = []
        self.loop_depth = 0  # >0 inside loop/foreach bodies (ack suppression)
        # Host-supplied randomness: deterministic kernel, clock-seeded host.
        # The tick advances per kernel call so loop iterations resample.
        if seed_base is None:
            seed_base = int(time.time() * 1000) % 2147483648
        self.seed_base = seed_base
        self.seed_tick = 0


def _script_seed(S):
    S.seed_tick += 1
    return (S.seed_base + S.seed_tick * 7919) % 2147483648


def _script_env_flat(S):
    names, values = [], []
    for frame in S.frames:
        for name, val in frame:
            names.append(name)
            values.append(val)
    return names, values


def _script_bind(S, name, val):
    top = S.frames[-1]
    for i, (n, _v) in enumerate(top):
        if n == name:
            top[i] = (name, val)
            return
    top.append((name, val))


def _script_kernel_eval(S, storage, node_id):
    names, values = _script_env_flat(S)
    ok, result, err = craft.script_eval_expr_boundary(
        S.nodes, S.kids, node_id, _elements_to_boundary(storage.get_all()),
        _load_recipes(), names, values, list(_script_new_reg), _script_seed(S),
    )
    if not ok:
        raise ScriptError(err)
    return [tuple(t) for t in result]


def _script_kernel_cond(S, storage, node_id):
    names, values = _script_env_flat(S)
    ok, truth, err = craft.script_eval_cond_boundary(
        S.nodes, S.kids, node_id, _elements_to_boundary(storage.get_all()),
        _load_recipes(), names, values, list(_script_new_reg), _script_seed(S),
    )
    if not ok:
        raise ScriptError(err)
    return truth


def _script_kernel_num(S, storage, node_id):
    names, values = _script_env_flat(S)
    ok, value, err = craft.script_eval_num_boundary(
        S.nodes, S.kids, node_id, _elements_to_boundary(storage.get_all()),
        _load_recipes(), names, values, list(_script_new_reg), _script_seed(S),
    )
    if not ok:
        raise ScriptError(err)
    return value


def _script_record_news(S, news):
    # AST-node granularity: the last completed mutating node owns [].
    global _script_new_reg
    _script_new_reg = list(news)
    for collector in S.collectors:
        collector.extend(news)


def _script_tuple_el(t):
    return Element(name=t[0], emoji=t[1] or "", is_first_discovery=bool(t[2]))


def _script_pairs_from_raw(raw_pairs):
    pairs = []
    for at, ae, af, bt, be, bf in raw_pairs:
        pairs.append((
            Element(name=at, emoji=ae, is_first_discovery=bool(af)),
            Element(name=bt, emoji=be, is_first_discovery=bool(bf)),
        ))
    return pairs


async def _script_run_pairs(S, client, storage, pairs, reason):
    global _bulk_confirm_resolved
    if not pairs:
        _bulk_confirm_resolved = True
        _repl_print_lines("  0 pairs — nothing to combine.")
        return [], []
    if craft.bulk_confirm_required(len(pairs), _BULK_WARN_THRESHOLD, _auto_approve):
        if not _interactive_mode_active:
            # Non-interactive runs have no y/n; announce instead of silently
            # burning budget (stress-test finding S3).
            _repl_print_lines(
                f"  {len(pairs)} pairs (over {_BULK_WARN_THRESHOLD}) — "
                "non-interactive run proceeds without confirm."
            )
        elif not await _prompt_continue(bulk_pending=True, reason=reason):
            raise CommandCancelled()
        _bulk_confirm_resolved = True
    else:
        if _auto_approve and craft.should_bulk_warn(len(pairs), _BULK_WARN_THRESHOLD):
            _repl_print_lines(
                _color(f"  Auto-approved {len(pairs)} pairs (/auto is on).", DIM)
            )
        _bulk_confirm_resolved = True
    collect = {"products": [], "news": [], "_seen_products": set()}
    await _combine_pairs(client, storage, pairs, collect=collect)
    if _cancelled:
        raise CommandCancelled()
    return collect["products"], collect["news"]


async def _script_combine_pair(S, client, storage, a_t, b_t):
    global _bulk_confirm_resolved
    _bulk_confirm_resolved = True  # single combines never bulk-confirm
    a = _script_tuple_el(a_t)
    b = _script_tuple_el(b_t)
    result = await _cached_pair(client, storage, a, b)
    products, news = [], []
    if result.name is not None:
        for elem in (a, b):
            storage.add(
                name=craft.sanitize_element_name(elem.name),
                emoji=elem.emoji,
                is_first_discovery=False,
            )
        known = storage.get_by_name(result.name) is not None
        storage.add(
            name=craft.sanitize_element_name(result.name),
            emoji=result.emoji,
            is_first_discovery=result.is_first_discovery,
        )
        tag = "" if known else " " + _color("[NEW]", BOLD + GREEN)
        hit = craft.is_target_hit(_target_element, result.name or "")
        if hit:
            tag += " " + _color("★ TARGET ★", BOLD + YELLOW + MAGENTA)
        _repl_print_lines(
            f"  {format_element(a)} + {format_element(b)} = {format_element(result)}{tag}"
        )
        products = [(result.name, result.emoji or "", bool(result.is_first_discovery))]
        if not known:
            news = list(products)
        if hit:
            await _acknowledge_target_hit(a.name, b.name, result.name or "")
    else:
        _repl_print_lines(f"  {format_element(a)} + {format_element(b)} = Nothing")
    _history.append((a.name, b.name, result.name if result.name else "Nothing"))
    _script_record_news(S, news)
    return products


def _script_union(a, b):
    return [tuple(t) for t in craft.script_union_tuples(list(a), list(b))]


async def _script_eval_operand(S, client, storage, node_id):
    if S.muts[node_id]:
        return await _script_eval(S, client, storage, node_id)
    return _script_kernel_eval(S, storage, node_id)


async def _script_eval(S, client, storage, node_id):
    _raise_if_cancelled()
    if not S.muts[node_id]:
        return _script_kernel_eval(S, storage, node_id)
    kind, a, b, c, sval = S.nodes[node_id]
    if kind == "assign":
        v = await _script_eval_operand(S, client, storage, a)
        _script_bind(S, sval, v)
        return v
    if kind == "union":
        acc = []
        for kid in S.kids[a]:
            acc = _script_union(acc, await _script_eval_operand(S, client, storage, kid))
        return acc
    if kind in ("diff", "intersect", "canrec", "cantrec"):
        left = await _script_eval_operand(S, client, storage, a)
        right = await _script_eval_operand(S, client, storage, b)
        return [tuple(t) for t in craft.script_set_op_boundary(kind, left, right, _load_recipes())]
    if kind == "first":
        v = await _script_eval_operand(S, client, storage, a)
        return [t for t in v if t[2]]
    if kind in ("take", "sample", "shuffle"):
        # Mutating inner: host walks it, then the kernel slices/samples.
        v = await _script_eval_operand(S, client, storage, a)
        if kind == "take":
            return [tuple(t) for t in craft.script_take_tuples(v, _script_kernel_num(S, storage, b))]
        n = len(v) if kind == "shuffle" else _script_kernel_num(S, storage, b)
        return [tuple(t) for t in craft.script_sample_tuples(v, n, _script_seed(S))]
    if kind == "newset":
        collector: list = []
        S.collectors.append(collector)
        try:
            await _script_eval_operand(S, client, storage, a)
        finally:
            S.collectors.pop()
        return _script_union(collector, [])
    if kind == "combine":
        left = await _script_eval_operand(S, client, storage, a)
        right = await _script_eval_operand(S, client, storage, b)
        if len(left) != 1 or len(right) != 1:
            raise ScriptError(
                f"+ combines single elements (left matched {len(left)}, "
                f"right matched {len(right)}) — use , to collect or * to cross"
            )
        return await _script_combine_pair(S, client, storage, left[0], right[0])
    if kind == "cross":
        left = await _script_eval_operand(S, client, storage, a)
        right = await _script_eval_operand(S, client, storage, b)
        pairs = _script_pairs_from_raw(craft.cross_pairs_boundary(left, right))
        products, news = await _script_run_pairs(S, client, storage, pairs, f"{len(pairs)} pairs")
        _script_record_news(S, news)
        return products
    if kind == "permute":
        v = await _script_eval_operand(S, client, storage, a)
        pairs = _script_pairs_from_raw(craft.permute_pairs_boundary(v))
        products, news = await _script_run_pairs(S, client, storage, pairs, f"{len(pairs)} pairs")
        _script_record_news(S, news)
        return products
    if kind == "exhaust":
        v = await _script_eval_operand(S, client, storage, a)
        pairs = _script_pairs_from_raw(
            craft.exhaust_pairs_boundary(v, _elements_to_boundary(storage.get_all()))
        )
        products, news = await _script_run_pairs(S, client, storage, pairs, f"{len(pairs)} pairs")
        _script_record_news(S, news)
        return products
    if kind == "permutate":
        pool = await _script_eval_operand(S, client, storage, a)
        products: list = []
        news_all: list = []
        while True:
            _raise_if_cancelled()
            pairs = _script_pairs_from_raw(craft.permute_pairs_boundary(pool))
            if not pairs:
                break
            round_products, round_news = await _script_run_pairs(
                S, client, storage, pairs, f"{len(pairs)} pairs per round"
            )
            products = _script_union(products, round_products)
            news_all = _script_union(news_all, round_news)
            if not round_news:
                break
            pool = _script_union(pool, round_news)
        _script_record_news(S, news_all)
        return products
    # crawl
    left = await _script_eval_operand(S, client, storage, a)
    right = await _script_eval_operand(S, client, storage, b)
    pool = _script_union(left, right)
    tried: list = []
    products = []
    news_all = []
    gen = 0
    while True:
        _raise_if_cancelled()
        raw_pairs, new_keys = craft.crawl_generation_pairs_boundary(pool, tried)
        tried.extend(new_keys)
        pairs = _script_pairs_from_raw(raw_pairs)
        if not pairs:
            break
        gen += 1
        _repl_print_lines(_color(f"  Gen {gen}: {len(pairs)} pairs to try...", DIM))
        gen_products, gen_news = await _script_run_pairs(
            S, client, storage, pairs, f"{len(pairs)} pairs this generation"
        )
        products = _script_union(products, gen_products)
        news_all = _script_union(news_all, gen_news)
        before = len(pool)
        pool = _script_union(pool, gen_products)
        grew = len(pool) - before
        plural = "" if grew == 1 else "s"
        _repl_print_lines(
            _color(f"  Gen {gen} done: {grew} element{plural} joined the pool.", DIM)
        )
        if grew == 0:
            break
    _script_record_news(S, news_all)
    return products


async def _script_exec_stmts(S, client, storage, kids_idx):
    for stmt in S.kids[kids_idx]:
        _raise_if_cancelled()
        await _script_exec_stmt(S, client, storage, stmt)


async def _script_exec_loop_body(S, client, storage, node_id):
    """Loop bodies run in the loop's own frame: a braced block gets no extra
    child scope here, so its walrus bindings reach the condition."""
    kind, a, b, c, sval = S.nodes[node_id]
    if kind == "block":
        await _script_exec_stmts(S, client, storage, a)
        return
    await _script_exec_stmt(S, client, storage, node_id)


async def _script_exec_stmt(S, client, storage, node_id):
    _raise_if_cancelled()
    kind, a, b, c, sval = S.nodes[node_id]
    if kind == "block":
        S.frames.append([])
        try:
            await _script_exec_stmts(S, client, storage, a)
        finally:
            S.frames.pop()
        return
    if kind == "assign":
        v = await _script_eval_operand(S, client, storage, a)
        _script_bind(S, sval, v)
        if S.loop_depth == 0:
            # Inside loops the ack would flood one line per iteration.
            plural = "" if len(v) == 1 else "s"
            _repl_print_lines(_color(f"  {sval} = {len(v)} element{plural}", DIM))
        return
    if kind == "foreach":
        vals = await _script_eval_operand(S, client, storage, a)
        S.loop_depth += 1
        try:
            for el in vals:
                await asyncio.sleep(0)  # yield so cancellation/input can run
                _raise_if_cancelled()
                S.frames.append([(sval, [el])])
                try:
                    await _script_exec_stmt(S, client, storage, b)
                finally:
                    S.frames.pop()
        finally:
            S.loop_depth -= 1
        return
    if kind in ("until", "while"):
        # A loop owns ONE scope shared by its body and condition: bindings
        # made by the body (braced or not) are visible to the test — the
        # spec's `{ n := [ ... ] } -> |n| < 2` idiom depends on it.
        S.frames.append([])
        S.loop_depth += 1
        try:
            iters = 0
            if kind == "until":
                while True:
                    await _script_exec_loop_body(S, client, storage, a)
                    iters += 1
                    # Pure bodies never await: yield so SIGINT handling and
                    # the input thread are not starved (stress-test BUG-1).
                    await asyncio.sleep(0)
                    _raise_if_cancelled()
                    if _script_kernel_cond(S, storage, b):
                        break
                plural = "" if iters == 1 else "s"
                _repl_print_lines(_color(f"  loop: condition met after {iters} iteration{plural}", DIM))
            else:
                while True:
                    await asyncio.sleep(0)
                    _raise_if_cancelled()
                    if not _script_kernel_cond(S, storage, b):
                        break
                    await _script_exec_loop_body(S, client, storage, a)
                    iters += 1
                if iters == 0:
                    _repl_print_lines(_color("  ~ loop: condition false, body skipped", DIM))
                else:
                    plural = "" if iters == 1 else "s"
                    _repl_print_lines(_color(f"  ~ loop: stopped after {iters} iteration{plural}", DIM))
        finally:
            S.frames.pop()
            S.loop_depth -= 1
        return
    if kind == "ternary":
        truth = _script_kernel_cond(S, storage, a)
        await _script_exec_stmt(S, client, storage, b if truth else c)
        return
    v = await _script_eval(S, client, storage, node_id)
    if not S.muts[node_id]:
        if not v:
            _repl_print_lines("  No matches found.")
        else:
            _repl_print_lines(
                "\n".join("  " + format_element(_script_tuple_el(t)) for t in v)
            )


async def _run_script(client, storage, source: str) -> bool:
    """Execute a script. Returns True when it ran to completion."""
    ok, nodes, kids, muts, err, pos = craft.script_parse(source)
    if not ok:
        _repl_print_lines(f"  {_color(f'Script error: {_tty(err)}', RED)}")
        return False
    S = _ScriptState(nodes, kids, muts)
    try:
        root = len(nodes) - 1
        await _script_exec_stmts(S, client, storage, nodes[root][1])
    except CommandCancelled:
        _repl_print_lines(f"  {_color('Cancelled.', YELLOW)}")
        return False
    except ScriptError as e:
        _repl_print_lines(f"  {_color(f'Script aborted: {_tty(str(e))}', RED)}")
        return False
    return True


async def _dispatch_line(client, storage, line: str) -> None:
    """Execute one input line from the API worker or immediate local commands."""
    if line == "/help":
        _repl_print_lines(do_help())
    elif (rest := craft.slash_args(line, "/search")) is not None:
        if not rest:
            msg = "  Usage: /search <query>"
        else:
            msg = do_search(storage, rest)
        _repl_print_lines(msg)
    elif (rest := craft.slash_args(line, "/recipe")) is not None:
        if not rest:
            msg = "  Usage: /recipe <element>"
        else:
            msg = do_recipe(storage, rest)
        _repl_print_lines(msg)
    elif line == "/list":
        _repl_print_lines(do_list(storage))
    elif (rest := craft.slash_args(line, "/permute")) is not None:
        if (err := _validate_command_line(line)) is not None:
            _repl_print_lines(err)
        else:
            await do_permute(client, storage, rest)
    elif (rest := craft.slash_args(line, "/permutate")) is not None:
        if (err := _validate_command_line(line)) is not None:
            _repl_print_lines(err)
        else:
            await do_permutate(client, storage, rest)
    elif (rest := craft.slash_args(line, "/import")) is not None:
        if (err := _validate_command_line(line)) is not None:
            msg = err
        else:
            msg = await do_import_async(storage, rest)
        _repl_print_lines(msg)
    elif (rest := craft.slash_args(line, "/unfilled")) is not None:
        _repl_print_lines(do_unfilled(storage))
    elif (rest := craft.slash_args(line, "/fill")) is not None:
        await _fill_missing_recipes_async(storage)
    elif (rest := craft.slash_args(line, "/prune")) is not None:
        await _prune_orphans_async(storage)
    elif (rest := craft.slash_args(line, "/export")) is not None:
        _repl_print_lines(do_export(storage, rest or EXPORT_PATH))
    elif (rest := craft.slash_args(line, "/lucky")) is not None:
        if (err := _validate_command_line(line)) is not None:
            _repl_print_lines(err)
        else:
            await do_lucky(client, storage, int(rest) if rest.strip() else 10)
    elif (rest := craft.slash_args(line, "/exhaust")) is not None:
        if (err := _validate_command_line(line)) is not None:
            _repl_print_lines(err)
        else:
            await do_exhaust(client, storage, rest)
    elif (rest := craft.slash_args(line, "/combine")) is not None:
        if (err := _validate_command_line(line)) is not None:
            msg = err
        else:
            first, second = craft.parse_two_elements(rest)
            msg = await do_combine(client, storage, first, second)
        _repl_print_lines(msg)
    elif (rest := craft.slash_args(line, "/crawl")) is not None:
        if (err := _validate_command_line(line)) is not None:
            _repl_print_lines(err)
        else:
            first, second = craft.parse_two_elements(rest)
            await do_crawl(client, storage, first, second)
    elif (rest := craft.slash_args(line, "/with")) is not None:
        if (err := _validate_command_line(line)) is not None:
            _repl_print_lines(err)
        else:
            element, query = craft.parse_with_args(rest)
            await do_with(client, storage, element, query)
    elif (rest := craft.slash_args(line, "/cross")) is not None:
        if (err := _validate_command_line(line)) is not None:
            _repl_print_lines(err)
        else:
            left_q, right_q = craft.parse_cross_queries(rest)
            await do_cross(client, storage, left_q, right_q)
    elif line == "/history":
        _repl_print_lines(do_history(storage))
    elif (rest := craft.slash_args(line, "/target")) is not None:
        _repl_print_lines(do_target(rest))
    elif (rest := craft.slash_args(line, "/auto")) is not None:
        _repl_print_lines(do_auto(rest))
    elif (rest := craft.slash_args(line, "/relay")) is not None:
        _repl_print_lines(do_relay(rest, storage))
    elif line == "/queue":
        _paint_queue_panel(force=True)
        if (
            not _chrome_enabled
            and not _current_command
            and not _current_ib_command
            and not list(_command_queue)
            and not list(_ib_command_queue)
        ):  # snapshot
            _repl_print_lines(do_queue_status())
    elif line == "/clear":
        if not _chrome_enabled:
            print(f"  {_color('(terminal has no output buffer to clear)', DIM)}")
        else:
            _chrome_sync()
    elif (rest := craft.slash_args(line, "/script")) is not None:
        if not rest:
            _repl_print_lines("  Usage: /script <path.ice>")
        else:
            try:
                source = Path(rest).expanduser().read_text(encoding="utf-8")
            except OSError as e:
                _repl_print_lines(f"  {_color(f'Cannot read script: {_tty(str(e))}', RED)}")
            else:
                await _run_script(client, storage, source)
    elif not craft.is_known_slash_command(line) and not line.startswith("/"):
        # Always-script REPL (v2): every non-slash line is a script.
        await _run_script(client, storage, line)
    elif not craft.is_known_slash_command(line) and craft.script_parse(line)[0]:
        # Lines like `/steam/ , fire*` start with "/" but are scripts.
        await _run_script(client, storage, line)
    else:
        _repl_print_lines(
            f"  Unknown input. Type {_color('/help', YELLOW)} for commands."
        )


class _LaneCfg:
    """Per-lane worker config: queue, current command, cancel-reset, bulk confirm."""

    __slots__ = ("lane", "bulk_confirm")

    def __init__(self, lane: str, *, bulk_confirm: bool = False):
        self.lane = lane
        self.bulk_confirm = bulk_confirm

    @property
    def queue(self) -> list:
        # Live global (interactive_mode may rebind the list).
        return _ib_command_queue if self.lane == "ib" else _command_queue

    def get_current(self) -> str:
        return _current_ib_command if self.lane == "ib" else _current_command

    def set_current(self, value: str) -> None:
        global _current_command, _current_ib_command
        if self.lane == "ib":
            _current_ib_command = value
        else:
            _current_command = value

    def cancel_reset(self) -> bool:
        """Start-of-job: clear shared cancel when peer lane is idle."""
        peer_busy = (
            bool(_current_command)
            if self.lane == "ib"
            else bool(_current_ib_command)
        )
        return craft.lane_should_reset_cancel(
            self.lane, peer_busy, _waiting_for_confirm()
        )

    def soft_cancel_reset(self) -> bool:
        """After soft-skip: pair always; IB only if pair idle."""
        peer_busy = (
            bool(_current_command)
            if self.lane == "ib"
            else bool(_current_ib_command)
        )
        return craft.lane_should_soft_reset_cancel(self.lane, peer_busy)


_pair_cfg = _LaneCfg("pair", bulk_confirm=True)
_ib_cfg = _LaneCfg("ib", bulk_confirm=False)


async def _lane_worker(cfg: _LaneCfg, client, storage):
    """Process one command lane (pair or IB) FIFO."""
    global _bulk_confirm_resolved, _skip_summary_shown
    while cfg.queue:
        # Don't clear cancel if the peer lane is mid-flight (shared _cancelled).
        if cfg.cancel_reset():
            _reset_cancelled()
        line = cfg.queue.pop(0)
        cfg.set_current(line)
        _skip_summary_shown = False
        if cfg.bulk_confirm:
            _bulk_confirm_resolved = not craft.may_bulk_confirm(line)
        _set_lane_progress(cfg.lane, 0, 0)
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
            cfg.set_current("")
            _clear_lane_progress(cfg.lane)
            _exit_cancel_scope()
            if _cancelled:
                if _discard_queue_after_cancel:
                    discarded = len(_command_queue) + len(_ib_command_queue)
                    _command_queue.clear()
                    _ib_command_queue.clear()
                    if discarded:
                        msg = f"  {_color(f'Cancelled. Discarded {discarded} queued command(s).', DIM)}"
                        _repl_print_lines(msg)
                    _mark_cancel_notified()
                    # The break skips the loop-top reset: clear the shared
                    # cancel here (peer-aware) or every later runs_local
                    # dispatch dies with a spurious "Cancelled."
                    # (stress-test round 4 session wedge).
                    if cfg.cancel_reset():
                        _reset_cancelled()
                    _paint_queue_panel(force=True)
                    break
                if not _skip_summary_shown:
                    msg = f"  {_color('Skipped.', YELLOW)}"
                    _repl_print_lines(msg)
                if cfg.soft_cancel_reset():
                    _reset_cancelled()


def _ensure_lane_worker(cfg: _LaneCfg, client, storage):
    global _api_worker_task, _ib_worker_task
    if cfg.lane == "ib":
        if _ib_worker_task is None or _ib_worker_task.done():
            _ib_worker_task = asyncio.create_task(_lane_worker(cfg, client, storage))
    else:
        if _api_worker_task is None or _api_worker_task.done():
            _api_worker_task = asyncio.create_task(_lane_worker(cfg, client, storage))


def _enqueue_command_line(line: str, client, storage) -> bool:
    """Append a line to the pair or IB queue. Returns True if enqueued."""
    if craft.is_known_slash_command(line):
        error = _validate_command_line(line)
        if error:
            _repl_print_lines(error)
            return False
    elif not craft.is_local_command(line):
        # Always-script REPL: parse (and static-check) before queueing so
        # errors surface immediately and broken scripts never run.
        ok, _nodes, _kids, _muts, err, _pos = craft.script_parse(line)
        if not ok:
            if line.lstrip().startswith("/"):
                # A slash-shaped line that is neither a known command nor a
                # parseable script is a typo'd command, not a script.
                _repl_print_lines(
                    f"  Unknown command. Type {_color('/help', YELLOW)} for commands."
                )
            else:
                _repl_print_lines(f"  {_color(f'Script error: {_tty(err)}', RED)}")
            return False
    lane = craft.command_queue_lane(line)
    ib = lane == "ib"
    if ib:
        q = list(_ib_command_queue)
        current = _current_ib_command
    else:
        q = list(_command_queue)
        current = _current_command
    decision = craft.queue_accept(
        line,
        current,
        list(q),
        len(_command_queue) + len(_ib_command_queue),
        _MAX_QUEUE_DEPTH,
    )
    if decision == "dup":
        msg = f"  {_color('Already queued.', DIM)}"
        _repl_print_lines(msg)
        return False
    if decision == "full":
        msg = f"  {_color(f'Queue full (max {_MAX_QUEUE_DEPTH}).', YELLOW)}"
        _repl_print_lines(msg)
        return False
    if craft.command_queue_lane(line) == "ib":
        current = _current_ib_command
        pending_len = len(_ib_command_queue)
        confirm_active = False
    else:
        current = _current_command
        pending_len = len(_command_queue)
        confirm_active = _waiting_for_confirm() or _bulk_confirm_pending
    deferred = craft.queue_lane_busy(current, pending_len, confirm_active)
    if not ib and not _current_command:
        _reset_cancelled()
    if ib:
        _ib_command_queue.append(line)
        _ensure_lane_worker(_ib_cfg, client, storage)
    else:
        _command_queue.append(line)
        _ensure_lane_worker(_pair_cfg, client, storage)
    if deferred and not _chrome_enabled:
        msg = f"  {_color(f'Queued: {_sanitize_queue_line(line)}', DIM)}"
        _repl_print_lines(msg)
    _chrome_sync()
    return True


async def _shutdown_interactive() -> int:
    """Cancel workers, discard queues, and print goodbye."""
    global _cancelled, _command_queue, _ib_command_queue, _api_worker_task
    _cancelled = True
    discarded = len(_command_queue) + len(_ib_command_queue)
    _command_queue.clear()
    _ib_command_queue.clear()
    await _cancel_and_await_worker(timeout=5.0)
    if discarded:
        msg = f"  Cancelled. Discarded {discarded} queued command(s)."
        _repl_print_lines(msg)
    _repl_print_lines("Goodbye!")
    return discarded


async def _cancel_and_await_worker(timeout: float = 2.0) -> None:
    """Cancel pair + IB workers (if running) and await completion. Idempotent."""
    global _api_worker_task, _ib_worker_task
    tasks = []
    for t in (_api_worker_task, _ib_worker_task):
        if t and not t.done():
            t.cancel()
            tasks.append(t)
    if tasks:
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), timeout=timeout
            )
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass
    _api_worker_task = None
    _ib_worker_task = None


async def _rate_ticker_loop() -> None:
    """Repaint sticky chrome on a short interval so the rate bar refills live."""
    try:
        while True:
            await asyncio.sleep(_RATE_TICK_SECONDS)
            if _interactive_mode_active:
                _paint_queue_panel()
    except asyncio.CancelledError:
        raise


async def interactive_mode():
    global _command_queue, _ib_command_queue, _current_command, _current_ib_command
    global _api_worker_task, _ib_worker_task, _cancelled
    global _confirm_future, _last_queue_snapshot, _queue_panel_height
    global _interactive_mode_active, _confirm_expected, _bulk_confirm_pending
    global _bulk_confirm_resolved, _confirm_answer_buffer
    global _active_client, _rate_ticker_task
    _interactive_mode_active = True
    _tty_reset_stdin_reader()
    _confirm_expected = False
    _bulk_confirm_pending = False
    _bulk_confirm_resolved = True
    _command_queue = []
    _ib_command_queue = []
    _current_command = ""
    _current_ib_command = ""
    _api_worker_task = None
    _ib_worker_task = None
    _confirm_future = None
    _last_queue_snapshot = ""
    _queue_panel_height = 0
    _active_client = None
    _rate_ticker_task = None

    print(
        _color("=== Infinite Craft CLI ===", BOLD + CYAN)
        + _color(f"  v{__version__}", DIM)
    )
    print()

    storage = DiscoveryStorage(DISCOVERIES_PATH)
    _patch_repl_print(True)
    _chrome_enable()
    _install_winch_handler()
    ki_exit = False
    global _main_task
    _main_task = asyncio.current_task()
    _sigint_installed = False
    try:
        asyncio.get_running_loop().add_signal_handler(signal.SIGINT, _session_sigint)
        _sigint_installed = True
    except NotImplementedError:
        signal.signal(signal.SIGINT, lambda *_: _session_sigint())
    try:
        async with InfiniteCraftClient(
            rate_limit=API_RATE_LIMIT,
            cancel_check=lambda: _cancelled,
            rate_limit_sleep_step=_RATE_LIMIT_SLEEP_STEP,
            _rate_limit_wait_callback=_rate_limit_wait_callback,
        ) as client:
            _active_client = client
            _rate_ticker_task = asyncio.create_task(_rate_ticker_loop())
            _relay_spawn_warmup(storage)
            global _bounty_task, _relay_warmup_task
            _bounty_task = asyncio.create_task(_beat_worker(client, storage))
            starters = "  ".join(format_element(e) for e in storage.get_all()[:4])
            _repl_print_lines(f"  Starting elements: {starters}")
            total = len(storage.get_all())
            _repl_print_lines(f"  Discovered: {_color(str(total), GREEN)} elements")
            _repl_print_lines(f"  Type {_color('/help', YELLOW)} for commands")

            while True:
                _paint_queue_panel()

                if (
                    (list(_command_queue) and not _current_command)
                    or (list(_ib_command_queue) and not _current_ib_command)
                ):  # snapshot for race safety — workers about to claim
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
                        if line in ("/quit", "/exit"):
                            await _shutdown_interactive()
                            break
                        if craft.is_local_command(line) or (
                            craft.runs_local(line)
                            and not _waiting_for_confirm()
                            and not _bulk_confirm_pending
                        ):
                            # Pure scripts interleave like locals, but never
                            # while a confirm waits: "y" parses as a pure
                            # script and must reach the confirm router.
                            if _cancelled and not _current_command and not _current_ib_command:
                                _reset_cancelled()
                            _echo_submitted_command(line)
                            await _dispatch_line(client, storage, line)
                            continue
                        if _route_confirm_input(line):
                            if _chrome_enabled:
                                with _repl_print_lock:
                                    _chrome_prompt = _craft_prompt()
                                    _chrome_refresh(force=True)
                            pass
                        elif line.strip():
                            _echo_submitted_command(line)
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

                if craft.is_local_command(line) or (
                    craft.runs_local(line)
                    and not _waiting_for_confirm()
                    and not _bulk_confirm_pending
                ):
                    if _cancelled and not _current_command and not _current_ib_command:
                        _reset_cancelled()
                    _echo_submitted_command(line)
                    await _dispatch_line(client, storage, line)
                    continue

                if _route_confirm_input(line):
                    continue

                _echo_submitted_command(line)
                _enqueue_command_line(line, client, storage)
    except asyncio.CancelledError:
        # Ctrl-C at an idle prompt: asyncio.run's Runner cancels this task.
        # If we let the cancellation propagate normally, Runner.close() then
        # joins the stdin reader thread — which is still blocked in read()
        # — and the process hangs forever (pre-existing since ≤1.9.2, where
        # it surfaced as a KeyboardInterrupt traceback). Flag it; the
        # finally below runs full teardown and exits directly.
        ki_exit = True
        raise
    finally:
        # Best-effort cleanup of worker on any exit (including uncaught exceptions or KI
        # during input) to avoid lingering high-memory processes/threads.
        if _rate_ticker_task is not None:
            _rate_ticker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await _rate_ticker_task
            _rate_ticker_task = None
        if _bounty_task is not None:
            _bounty_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await _bounty_task
            _bounty_task = None
        if _relay_warmup_task is not None:
            _relay_warmup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await _relay_warmup_task
            _relay_warmup_task = None
        for _t in list(_relay_bg_tasks):
            _t.cancel()
        for _t in list(_relay_bg_tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await _t
        _relay_bg_tasks.clear()
        _active_client = None
        await _cancel_and_await_worker()
        _teardown_tty_and_chrome()
        _confirm_future = None
        _main_task = None
        if _sigint_installed:
            with contextlib.suppress(Exception):
                asyncio.get_running_loop().remove_signal_handler(signal.SIGINT)
        if ki_exit:
            # Teardown is done and the save is on disk; skip the Runner's
            # doomed join of the blocked reader thread.
            _builtin_print("\n  Goodbye!")
            sys.stdout.flush()
            os._exit(130)


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
            _relay_spawn_warmup(storage)
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
            elif args.command == "lucky":
                await do_lucky(client, storage, args.count)
            elif args.command == "script":
                if args.file:
                    try:
                        source = Path(args.file).expanduser().read_text(encoding="utf-8")
                    except OSError as e:
                        print(f"  Cannot read script: {e}")
                        raise SystemExit(1) from None
                else:
                    source = args.source
                if not await _run_script(client, storage, source):
                    raise SystemExit(1)


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

    lucky_p = subparsers.add_parser(
        "lucky", help="Try random untried pairs (entropy mining)"
    )
    lucky_p.add_argument("count", nargs="?", type=int, default=10, help="Pairs to try (default 10)")

    script_p = subparsers.add_parser(
        "script", help="Run an Infinite Craft script (spec v0.6)"
    )
    script_p.add_argument("source", nargs="?", default="", help="Script source text")
    script_p.add_argument("-f", "--file", help="Path to a .ice script file")

    args = parser.parse_args()

    if args.command is None:
        try:
            asyncio.run(interactive_mode())
        except KeyboardInterrupt:
            # Ctrl-C at an idle prompt (or in the race window between a
            # command finishing and the next prompt) used to escape as a
            # raw traceback — pre-existing since at least 1.9.2. Exit like
            # /quit instead; the save is already on disk.
            print("\n  Goodbye!")
            raise SystemExit(130) from None
    else:
        try:
            asyncio.run(noninteractive_mode(args))
        except KeyboardInterrupt:
            # Clean cancel instead of a traceback; partial progress is
            # already saved (storage writes land as they happen).
            print("\n  Cancelled.")
            raise SystemExit(130) from None


if __name__ == "__main__":
    main()
