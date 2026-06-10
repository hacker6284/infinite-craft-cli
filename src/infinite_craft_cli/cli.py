#!/usr/bin/env python3
"""Infinite Craft CLI — combine elements from the terminal or as a scripted tool."""

import asyncio
import argparse
import fnmatch
import gzip
import re
import json
import os
import signal
import sys
import time

try:
    import readline  # noqa: F401 — enables arrow keys, history in input()
except ImportError:
    pass  # readline not available on Windows

from infinite_craft_cli.element import Element
from infinite_craft_cli.client import InfiniteCraftClient, fetch_json, clear_fetch_cache, _get_sync_session
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

API_RATE_LIMIT = 60   # requests per minute — conservative to avoid Cloudflare blocks
API_CONCURRENCY = 2   # parallel workers for bulk operations
MAX_QUERY_LENGTH = 512
MAX_REGEX_BODY_LENGTH = 200
REGEX_TIMEOUT = 0.02
MATCH_SCAN_BUDGET = 0.5
REGEX_ERROR_INVALID = "Invalid regex pattern"
REGEX_ERROR_COMPLEX = "Regex pattern too complex"
_QUERY_HELP = "Search query (wildcards, /regex/, ! or ^ for first discoveries)"

import regex as _regex_module

_RE_NESTED_QUANTIFIER = re.compile(r"(\+|\*|\?|\{\d*,?\d*\})\s*(\+|\*|\?|\{)")
_RE_DELIMITED_REGEX = re.compile(r"/[^/]+/")

# Session-only history
_history: list[tuple[str, str, str]] = []


# ---------------------------------------------------------------------------
# Persistent recipe store
# ---------------------------------------------------------------------------
def _load_recipes() -> dict[str, list[list[str]]]:
    """Load recipes.json: {result_name: [[a_name, b_name], ...]}"""
    if os.path.exists(RECIPES_PATH):
        with open(RECIPES_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_recipes(recipes: dict[str, list[list[str]]]):
    with open(RECIPES_PATH, "w", encoding="utf-8") as f:
        json.dump(recipes, f, indent=2)


def _record_recipe(result_name: str, a_name: str, b_name: str):
    """Record that a_name + b_name = result_name."""
    recipes = _load_recipes()
    pair = sorted([a_name, b_name])
    if result_name not in recipes:
        recipes[result_name] = []
    if pair not in recipes[result_name]:
        recipes[result_name].append(pair)
        _save_recipes(recipes)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------
def _color(text: str, code: str) -> str:
    if sys.stdout.isatty():
        return f"{code}{text}{RESET}"
    return text


def format_element(elem) -> str:
    s = str(elem)  # uses Element.__str__ which handles emoji
    if elem.is_first_discovery:
        s += " " + _color("[FIRST DISCOVERY!]", BOLD + MAGENTA)
    return s


def format_result(first_name: str, second_name: str, result) -> str:
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


async def _cached_pair(client, storage, a, b):
    """Wrapper around client.pair that caches results by sorted element names."""
    key = tuple(sorted([a.name, b.name]))
    if key in _pair_cache:
        return _pair_cache[key]
    for attempt in range(3):
        try:
            result = await client.pair(a.name, b.name)
            break
        except Exception:
            if attempt == 2:
                raise
            await asyncio.sleep(2 ** attempt)
    _pair_cache[key] = result
    if result.name is not None:
        _record_recipe(result.name, a.name, b.name)
    return result


async def do_combine(client, storage, first_name: str, second_name: str) -> str:
    first = _resolve_element(storage, first_name)
    second = _resolve_element(storage, second_name)
    try:
        result = await _cached_pair(client, storage, first, second)
    except Exception as e:
        return _color(f"  Error: {e}", RED)
    # If the pairing succeeded, ensure both inputs and result are in discoveries
    if result.name is not None:
        for elem in (first, second):
            storage.add(
                name=elem.name, emoji=elem.emoji, is_first_discovery=False
            )
        storage.add(
            name=result.name, emoji=result.emoji, is_first_discovery=result.is_first_discovery
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
        return line[len(prefix):]
    return None


def _parse_query_filter(query: str) -> tuple[str, bool]:
    """Parse a query, returning (pattern, first_discoveries_only).

    Prefix ``!`` limits to first discoveries only (``^`` is accepted as legacy).
    """
    q = query.strip()
    only_new = False
    if q.startswith("!"):
        only_new = True
        q = q[1:]
    elif q.startswith("^"):
        only_new = True
        q = q[1:]
    return q, only_new


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
    q, only_new = _parse_query_filter(query)
    if not q.strip():
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
        if matched:
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
        return f"  {target} not found in discoveries."
    target = elem.name

    if target in _BASE_ELEMENTS:
        return f"  {target} is a base element."

    if target not in recipes or not recipes.get(target):
        return f"  No recipe known for {target}. Try /fill or /import."

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
        return (
            n in visited
            or n in _BASE_ELEMENTS
            or not recipes.get(n)
        )

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
        return f"  Cannot trace full lineage for {target} — missing intermediate recipes."

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

    lines = [f"  Recipe for {_color(target, BOLD)} ({len(steps)} steps):"]
    for a, b, r in steps:
        a_elem = storage.get_by_name(a)
        b_elem = storage.get_by_name(b)
        r_elem = storage.get_by_name(r)
        a_str = str(a_elem) if a_elem else a
        b_str = str(b_elem) if b_elem else b
        r_str = format_element(r_elem) if r_elem else r
        lines.append(f"    {a_str} + {b_str} = {r_str}")
    return "\n".join(lines)


def do_list(storage) -> str:
    discoveries = storage.get_all()
    header = f"  Discovered {len(discoveries)} elements:"
    lines = [f"  {format_element(e)}" for e in discoveries]
    return header + "\n" + "\n".join(lines)


def do_history() -> str:
    if not _history:
        return "  No combinations tried yet."
    lines = []
    for i, (a, b, r) in enumerate(_history, 1):
        lines.append(f"  {i}. {a} + {b} = {r}")
    return "\n".join(lines)


async def do_crawl(client, storage, first_name: str, second_name: str):
    """Combine two elements, then iteratively combine results with all inputs until nothing new."""
    first = _resolve_element(storage, first_name)
    second = _resolve_element(storage, second_name)
    pool = {first.name: first, second.name: second}
    tried = set()
    generation = 0

    print(f"  Crawling from {_color(str(first), BOLD)} and {_color(str(second), BOLD)}...")
    print(f"  (Ctrl+C to stop)\n")

    while True:
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
            print(f"\n  Exhausted all pairs. {len(pool)} elements in pool.")
            break

        print(f"  --- Generation {generation}: {len(new_pairs)} new pairs to try ---")

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
        print(f"  +{new_count} new ({len(pool)} in pool)\n")

        if new_count == 0 or _cancelled:
            if _cancelled:
                print(f"  Stopped.")
            else:
                print(f"  No new discoveries. Stopping.")
            break

    print(f"  Final pool ({len(pool)}):")
    for name in sorted(pool.keys()):
        print(f"    {pool[name]}")


async def do_exhaust(client, storage, name: str):
    """Combine an element with every discovered element."""
    target = _resolve_element(storage, name)
    others = list(storage.get_all())
    pairs = [(target, o) for o in others if o.name != target.name]
    if not pairs:
        print(f"  No other elements to combine with {_color(str(target), BOLD)}.")
        return
    print(f"  Combining {_color(str(target), BOLD)} with {len(pairs)} elements...")
    await _confirm_and_run_pairs(client, storage, pairs)


async def do_with(client, storage, element_name: str, query: str):
    """Combine an element with all discoveries matching a query."""
    target = _resolve_element(storage, element_name)
    others, err = _match_elements(storage, query)
    if err:
        print(f"  {err}")
        return
    if not others:
        print(f"  No elements match: {query}")
        return
    pairs = [(target, o) for o in others if o.name != target.name]
    if not pairs:
        print(f"  No other elements match: {query}")
        return
    print(
        f"  Combining {_color(str(target), BOLD)} with {len(pairs)} elements "
        f"matching {_color(query, YELLOW)}..."
    )
    await _confirm_and_run_pairs(client, storage, pairs)


def _parse_two_elements(rest: str) -> tuple[str, str] | None:
    """Parse two element names from ``<el> + <el>`` or ``<el> <el>``."""
    rest = rest.strip()
    if " + " in rest:
        parts = rest.split(" + ", 1)
        first, second = parts[0].strip(), parts[1].strip()
    else:
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
    """Parse two queries from ``<query> * <query>`` or ``<query> <query>``."""
    rest = rest.strip()
    if " * " in rest:
        parts = rest.split(" * ", 1)
        left, right = parts[0].strip(), parts[1].strip()
    elif _contains_delimited_regex(rest):
        # Delimited regex queries may contain spaces; require explicit * delimiter.
        return None
    else:
        parts = rest.split(None, 1)
        if len(parts) != 2:
            return None
        left, right = parts[0].strip(), parts[1].strip()
    if not left or not right:
        return None
    return left, right


_BULK_WARN_THRESHOLD = 200


_cancelled = False


async def _combine_pairs(client, storage, pairs: list[tuple]):
    """Combine a list of (element, element) pairs with light parallelism."""
    global _cancelled
    _cancelled = False
    loop = asyncio.get_running_loop()
    original_handler = None

    def on_sigint():
        global _cancelled
        _cancelled = True

    # Install SIGINT handler that sets flag instead of raising
    try:
        original_handler = loop.add_signal_handler(signal.SIGINT, on_sigint)
    except NotImplementedError:
        signal.signal(signal.SIGINT, lambda *_: on_sigint())

    total = len(pairs)
    new_count = 0
    nothing_count = 0
    done_count = 0
    known_names = {e.name for e in storage.get_all()}
    sem = asyncio.Semaphore(API_CONCURRENCY)
    lock = asyncio.Lock()

    async def process(a, b):
        nonlocal new_count, nothing_count, done_count
        try:
            result = await _cached_pair(client, storage, a, b)
        except Exception as e:
            done_count += 1
            print(f"  [{done_count}/{total}] {a} + {b} = {_color(f'Error: {e}', RED)}")
            return
        done_count += 1
        if result.name is not None:
            for elem in (a, b):
                storage.add(
                    name=elem.name, emoji=elem.emoji, is_first_discovery=False
                )
            storage.add(
                name=result.name, emoji=result.emoji, is_first_discovery=result.is_first_discovery
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
            print(f"  [{done_count}/{total}] {a} + {b} = {format_element(result)}{tag}")

    # Process in batches of API_CONCURRENCY to avoid overwhelming the rate limiter
    for i in range(0, len(pairs), API_CONCURRENCY):
        if _cancelled:
            break
        batch = pairs[i:i + API_CONCURRENCY]
        await asyncio.gather(*(process(a, b) for a, b in batch))

    # Restore default SIGINT handler
    try:
        loop.remove_signal_handler(signal.SIGINT)
    except (NotImplementedError, ValueError):
        pass

    if _cancelled:
        print(f"\n  Cancelled. {_color(str(new_count), GREEN)} new, {nothing_count} nothing, {done_count}/{total} tried.")
    else:
        print(f"\n  Done. {_color(str(new_count), GREEN)} new, {nothing_count} nothing, {total} tried.")


async def _confirm_and_run_pairs(client, storage, pairs: list[tuple]):
    """Warn if too many pairs, then run them."""
    if len(pairs) > _BULK_WARN_THRESHOLD:
        print(f"\n  {_color(f'Warning: this will make {len(pairs)} API requests.', YELLOW)}")
        if sys.stdin.isatty():
            try:
                answer = input("  Continue? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n  Cancelled.")
                return
            if answer not in ("y", "yes"):
                print("  Cancelled.")
                return
    print()
    await _combine_pairs(client, storage, pairs)


async def do_permute(client, storage, query: str):
    """Combine every pair of elements matching the query with each other."""
    matches, err = _match_elements(storage, query)
    if err:
        print(f"  {err}")
        return
    if not matches:
        print("  No elements match that query.")
        return
    if len(matches) == 1:
        print(f"  Only one match: {format_element(matches[0])}. Need at least two.")
        return

    n = len(matches)
    pairs = [(matches[i], matches[j]) for i in range(n) for j in range(i + 1, n)]
    print(f"  {n} elements match, {len(pairs)} unique pairs:")
    for m in matches:
        print(f"    {format_element(m)}")
    await _confirm_and_run_pairs(client, storage, pairs)


async def do_cross(client, storage, left_query: str, right_query: str):
    """Cross-combine all elements matching left_query with all matching right_query."""
    left, left_err = _match_elements(storage, left_query)
    if left_err:
        print(f"  {left_err}")
        return
    right, right_err = _match_elements(storage, right_query)
    if right_err:
        print(f"  {right_err}")
        return
    if not left:
        print(f"  No elements match: {left_query}")
        return
    if not right:
        print(f"  No elements match: {right_query}")
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
        print("  No valid pairs (all matches overlap).")
        return

    print(f"  Left ({len(left)}): {', '.join(str(e) for e in left[:10])}{'...' if len(left) > 10 else ''}")
    print(f"  Right ({len(right)}): {', '.join(str(e) for e in right[:10])}{'...' if len(right) > 10 else ''}")
    print(f"  {len(pairs)} unique pairs")
    await _confirm_and_run_pairs(client, storage, pairs)


# ---------------------------------------------------------------------------
# Infinibrowser integration
# ---------------------------------------------------------------------------
_IB_BASE = "https://infinibrowser.wiki/api"


def _ib_fetch(path: str, params: dict, use_cache: bool = True) -> dict | None:
    """Fetch from the Infinibrowser API. Prints errors on failure."""
    result = fetch_json(f"{_IB_BASE}/{path}", params=params, use_cache=use_cache)
    if result is None:
        print(f"  {_color('Infinibrowser request failed', RED)}")
    return result


def _ib_fetch_quiet(path: str, params: dict) -> dict | None:
    """Fetch from the Infinibrowser API. Silent on errors."""
    return fetch_json(f"{_IB_BASE}/{path}", params=params)


def _import_from_infinibrowser(storage, name: str) -> str:
    """Look up an element on Infinibrowser, show its lineage, and import into discoveries."""
    data = _ib_fetch("item", {"id": name})
    if data is None:
        return ""
    if "code" in data:
        return f"  {_color('Not found', DIM)} on Infinibrowser: {name}"

    emoji = data.get("emoji", "")
    depth = data.get("depth", "?")
    print(f"  Found: {emoji} {data['text']}  (depth {depth})")

    lineage = _ib_fetch("recipe", {"id": name}, use_cache=False)
    if lineage is None:
        return ""
    steps = lineage.get("steps", [])
    if not steps:
        return f"  No lineage available for {name}."

    print(f"  Lineage ({len(steps)} steps):")
    imported = set()
    for step in steps:
        a_name, a_emoji = step["a"]["id"], step["a"]["emoji"]
        b_name, b_emoji = step["b"]["id"], step["b"]["emoji"]
        r_name, r_emoji = step["result"]["id"], step["result"]["emoji"]
        print(f"    {a_emoji} {a_name} + {b_emoji} {b_name} = {r_emoji} {r_name}")
        _record_recipe(r_name, a_name, b_name)
        for elem_name, elem_emoji in [(a_name, a_emoji), (b_name, b_emoji), (r_name, r_emoji)]:
            if elem_name not in imported:
                storage.add(
                    name=elem_name, emoji=elem_emoji, is_first_discovery=False
                )
                imported.add(elem_name)

    storage.reload()
    return f"  Imported {_color(str(len(imported)), GREEN)} elements into discoveries."


def _import_from_save(storage, path: str) -> str:
    """Import elements and recipes from an .ic save file into discoveries."""
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            save = json.load(f)
    except Exception as e:
        return f"  {_color(f'Error reading save file: {e}', RED)}"

    items = save.get("items", [])
    if not items:
        return "  No items in save file."

    # Build id-to-name lookup
    id_to_item = {item["id"]: item for item in items}

    imported_count = 0
    recipe_count = 0
    for item in items:
        name = item["text"]
        emoji = item.get("emoji", "")
        is_discovery = item.get("discovery", False)
        result = storage.add(
            name=name, emoji=emoji, is_first_discovery=is_discovery
        )
        if result is not None:
            imported_count += 1
        # Import recipes
        for recipe in item.get("recipes", []):
            if len(recipe) == 2 and recipe[0] in id_to_item and recipe[1] in id_to_item:
                a_name = id_to_item[recipe[0]]["text"]
                b_name = id_to_item[recipe[1]]["text"]
                _record_recipe(name, a_name, b_name)
                recipe_count += 1

    storage.reload()
    total = len(items)
    return (f"  Loaded {_color(str(total), GREEN)} elements "
            f"({imported_count} new) with {recipe_count} recipes from {_color(path, BOLD)}")


def do_import(storage, arg: str) -> str:
    """Import from Infinibrowser (element name) or .ic save file (path)."""
    if arg.endswith(".ic") or os.path.sep in arg:
        return _import_from_save(storage, arg)
    return _import_from_infinibrowser(storage, arg)


_BASE_ELEMENTS = {"Water", "Fire", "Wind", "Earth"}


def _fill_missing_recipes(storage):
    """Fetch lineages from Infinibrowser for elements missing recipes.

    When a lineage is fetched, its intermediate elements get recipes too,
    so we re-check the missing set after each fetch to skip already-filled items.
    """
    recipes = _load_recipes()
    name_set = {e.name for e in storage.get_all()}
    missing = {e.name for e in storage.get_all()
               if e.name not in _BASE_ELEMENTS and e.name not in recipes}
    if not missing:
        print("  All elements have recipes.")
        return

    total = len(missing)
    print(f"  {total} elements missing recipes. Fetching from Infinibrowser...")
    print(f"  (Ctrl+C to stop early)\n")
    fetched = 0
    skipped = 0
    failed = set()
    processed = 0
    queue = sorted(missing)
    try:
        for name in queue:
            # Re-check: a previous lineage may have filled this one
            recipes = _load_recipes()
            if name in recipes or name in failed:
                skipped += 1
                continue
            processed += 1
            remaining = total - fetched - skipped - len(failed)
            print(f"\r  [{processed}/{total}] {name} ({remaining} remaining)...          ", end="", flush=True)
            data = _ib_fetch_quiet("item", {"id": name})
            if data is None or "code" in data:
                failed.add(name)
                continue
            lineage = _ib_fetch_quiet("recipe", {"id": name})
            if lineage is None:
                failed.add(name)
                continue
            for step in lineage.get("steps", []):
                a_name, a_emoji = step["a"]["id"], step["a"]["emoji"]
                b_name, b_emoji = step["b"]["id"], step["b"]["emoji"]
                r_name, r_emoji = step["result"]["id"], step["result"]["emoji"]
                _record_recipe(r_name, a_name, b_name)
                for elem_name, elem_emoji in [(a_name, a_emoji), (b_name, b_emoji), (r_name, r_emoji)]:
                    if elem_name not in name_set:
                        storage.add(
                            name=elem_name, emoji=elem_emoji, is_first_discovery=False
                        )
                        name_set.add(elem_name)
            fetched += 1
            time.sleep(0.5)  # rate limit Infinibrowser
    except KeyboardInterrupt:
        print(f"\n  Stopped early.")
    storage.reload()
    print(f"\n  Fetched {fetched} lineages, {skipped} already filled by prior lineages.", end="")
    if failed:
        print(f" {_color(str(len(failed)), YELLOW)} not found on Infinibrowser.")
    else:
        print()


def do_unfilled(storage) -> str:
    """List elements that have no recipes (excluding base elements)."""
    recipes = _load_recipes()
    discoveries = storage.get_all()
    missing = [e for e in discoveries if e.name not in _BASE_ELEMENTS and e.name not in recipes]
    if not missing:
        return "  All elements have recipes."
    lines = [f"  {len(missing)} elements without recipes:\n"]
    for e in missing:
        lines.append(f"    {format_element(e)}")
    return "\n".join(lines)


def _included_element_names(recipes: dict[str, list[list[str]]] | None = None) -> set[str]:
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


def _prune_orphans(storage):
    """Remove orphan discoveries that Infinibrowser confirms have no recipe."""
    candidates = _orphan_candidates(storage)
    if not candidates:
        print("  Nothing to prune.")
        return

    total = len(candidates)
    print(f"  {total} orphan element{'s' if total != 1 else ''} to check on Infinibrowser...")
    print(f"  (Ctrl+C to stop early)\n")
    pruned = 0
    skipped = 0
    kept = 0
    try:
        for i, elem in enumerate(candidates, 1):
            print(f"\r  [{i}/{total}] {elem.name}...          ", end="", flush=True)
            fillable = _ib_can_fill(elem.name)
            if fillable is None:
                skipped += 1
            elif fillable:
                kept += 1
            else:
                storage.remove(elem.name)
                pruned += 1
            time.sleep(0.5)
    except KeyboardInterrupt:
        print(f"\n  Stopped early.")
    print(f"\n  Pruned {_color(str(pruned), GREEN)} element{'s' if pruned != 1 else ''}.", end="")
    if kept:
        print(f" {kept} fillable on Infinibrowser (kept).", end="")
    if skipped:
        print(f" {_color(str(skipped), YELLOW)} skipped (API errors).", end="")
    print()


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

    msg = f"  Exported {_color(str(len(items)), GREEN)} elements to {_color(path, BOLD)}"
    if excluded:
        msg += f"\n  {_color(str(excluded), YELLOW)} elements excluded (no recipes and not referenced by any included recipe — use /fill to fetch them)"
    return msg


def do_help() -> str:
    return """  Combine:
    <element> + <element>       Combine two elements
    /combine <el> + <el>        Same (also: /combine <el> <el>)

  Crawl:
    <element> ++ <element>      Combine & crawl until no new discoveries
    /crawl <el> + <el>          Same as ++ (also: /crawl <el> <el>)

  Bulk combine (query syntax below):
    <element> +| <query>        Combine element with all matching discoveries
    <element> + | <query>       Same as +| (spaced variant)
    /with <element> <query>     Same as +|
    <query> * <query>           Cross-combine matches from both queries
    /cross <query> * <query>    Same as * (also: /cross <q> <q>)
    /permute <query>            Combine all matching elements with each other
    /exhaust <element>          Combine element with all discoveries

  Query syntax (/search, /with, /permute, /cross, shorthands):
    substring                   Default: case-insensitive substring
    * ? []                      fnmatch wildcards (e.g. fire*, mu?)
    /pattern/                   Regex, case-insensitive (no | alternation)
    !<query>                    First discoveries only (e.g. !fire*)
    ^<query>                    Legacy alias for !<query>

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
    /help                       Show this help
    /quit                       Exit"""


# ---------------------------------------------------------------------------
# Interactive REPL
# ---------------------------------------------------------------------------
async def interactive_mode():
    print(_color("=== Infinite Craft CLI ===", BOLD + CYAN))
    print()

    storage = DiscoveryStorage(DISCOVERIES_PATH)
    async with InfiniteCraftClient(rate_limit=API_RATE_LIMIT) as client:
        starters = "  ".join(str(e) for e in storage.get_all()[:4])
        print(f"  Starting elements: {starters}")
        total = len(storage.get_all())
        print(f"  Discovered: {_color(str(total), GREEN)} elements")
        print(f"  Type {_color('/help', YELLOW)} for commands\n")

        while True:
            try:
                line = input(_color("craft> ", CYAN)).strip()
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye!")
                break

            if not line:
                continue

            if line in ("/quit", "/exit"):
                print("Goodbye!")
                break
            elif line == "/help":
                print(do_help())
            elif (rest := _slash_args(line, "/search")) is not None:
                if not rest:
                    print("  Usage: /search <query>")
                else:
                    print(do_search(storage, rest))
            elif (rest := _slash_args(line, "/recipe")) is not None:
                if not rest:
                    print("  Usage: /recipe <element>")
                else:
                    print(do_recipe(storage, rest))
            elif line == "/list":
                print(do_list(storage))
            elif (rest := _slash_args(line, "/permute")) is not None:
                if not rest:
                    print("  Usage: /permute <query>")
                else:
                    await do_permute(client, storage, rest)
            elif (rest := _slash_args(line, "/import")) is not None:
                if not rest:
                    print("  Usage: /import <element>")
                else:
                    print(do_import(storage, rest))
            elif (rest := _slash_args(line, "/unfilled")) is not None:
                print(do_unfilled(storage))
            elif (rest := _slash_args(line, "/fill")) is not None:
                _fill_missing_recipes(storage)
            elif (rest := _slash_args(line, "/prune")) is not None:
                _prune_orphans(storage)
            elif (rest := _slash_args(line, "/export")) is not None:
                print(do_export(storage, rest or EXPORT_PATH))
            elif (rest := _slash_args(line, "/exhaust")) is not None:
                if not rest:
                    print("  Usage: /exhaust <element>")
                else:
                    await do_exhaust(client, storage, rest)
            elif (rest := _slash_args(line, "/combine")) is not None:
                parsed = _parse_two_elements(rest)
                if parsed is None:
                    print("  Usage: /combine <element> + <element>")
                else:
                    first, second = parsed
                    print(await do_combine(client, storage, first, second))
            elif (rest := _slash_args(line, "/crawl")) is not None:
                parsed = _parse_two_elements(rest)
                if parsed is None:
                    print("  Usage: /crawl <element> + <element>")
                else:
                    first, second = parsed
                    await do_crawl(client, storage, first, second)
            elif (rest := _slash_args(line, "/with")) is not None:
                parsed = _parse_with_args(rest)
                if parsed is None:
                    print("  Usage: /with <element> <query>")
                else:
                    element, query = parsed
                    await do_with(client, storage, element, query)
            elif (rest := _slash_args(line, "/cross")) is not None:
                parsed = _parse_cross_queries(rest)
                if parsed is None:
                    print("  Usage: /cross <query> * <query>")
                else:
                    left_q, right_q = parsed
                    await do_cross(client, storage, left_q, right_q)
            elif line == "/history":
                print(do_history())
            elif " ++ " in line:
                parts = line.split(" ++ ", 1)
                first = parts[0].strip()
                second = parts[1].strip()
                if not first or not second:
                    print("  Usage: <element> ++ <element>")
                else:
                    await do_crawl(client, storage, first, second)
            elif re.search(r"\+\s*\|", line):
                parts = re.split(r"\+\s*\|", line, 1)
                name = parts[0].strip()
                query = parts[1].strip()
                if not name or not query:
                    print("  Usage: <element> +| <query>")
                else:
                    await do_with(client, storage, name, query)
            elif " * " in line:
                # query * query
                parts = line.split(" * ", 1)
                left_q = parts[0].strip()
                right_q = parts[1].strip()
                if not left_q or not right_q:
                    print("  Usage: <query> * <query>")
                else:
                    await do_cross(client, storage, left_q, right_q)
            elif " + " in line:
                parts = line.split(" + ", 1)
                first = parts[0].strip()
                second = parts[1].strip()
                if not first or not second:
                    print("  Usage: <element> + <element>")
                else:
                    print(await do_combine(client, storage, first, second))
            else:
                print(f"  Unknown input. Type {_color('/help', YELLOW)} for commands.")


# ---------------------------------------------------------------------------
# Non-interactive CLI
# ---------------------------------------------------------------------------
async def noninteractive_mode(args):
    # Commands that only need storage (no API)
    storage_only_commands = {"search", "list", "recipe", "unfilled", "export", "fill", "prune", "import_cmd"}

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
            _fill_missing_recipes(storage)
        elif args.command == "prune":
            _prune_orphans(storage)
        elif args.command == "import_cmd":
            print(do_import(storage, args.source))
    else:
        # Commands that need the API client
        storage = DiscoveryStorage(DISCOVERIES_PATH)
        async with InfiniteCraftClient(rate_limit=API_RATE_LIMIT) as client:
            if args.command == "combine":
                print(await do_combine(client, storage, args.first, args.second))
            elif args.command == "exhaust":
                await do_exhaust(client, storage, args.name)
            elif args.command == "crawl":
                await do_crawl(client, storage, args.first, args.second)
            elif args.command == "permute":
                await do_permute(client, storage, args.query)
            elif args.command == "cross":
                await do_cross(client, storage, args.left, args.right)
            elif args.command == "with":
                await do_with(client, storage, args.element, args.query)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Infinite Craft CLI")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    combine_p = subparsers.add_parser("combine", help="Combine two elements")
    combine_p.add_argument("first", help="First element name")
    combine_p.add_argument("second", help="Second element name")

    search_p = subparsers.add_parser("search", help="Search discovered elements")
    search_p.add_argument("query", help=_QUERY_HELP)

    subparsers.add_parser("list", help="List all discovered elements")

    recipe_p = subparsers.add_parser("recipe", help="Show shortest recipe from base elements")
    recipe_p.add_argument("name", help="Element name")

    import_p = subparsers.add_parser("import", help="Import from Infinibrowser or .ic save file")
    import_p.add_argument("source", help="Element name or path to .ic file")
    import_p.set_defaults(command="import_cmd")

    export_p = subparsers.add_parser("export", help="Export discoveries as .ic save file")
    export_p.add_argument("path", nargs="?", default=None, help="Output path (optional)")

    subparsers.add_parser("fill", help="Fetch missing recipes from Infinibrowser")

    subparsers.add_parser("unfilled", help="List elements without recipes")

    subparsers.add_parser("prune", help="Remove orphan elements Infinibrowser can't fill")

    exhaust_p = subparsers.add_parser("exhaust", help="Combine element with all discoveries")
    exhaust_p.add_argument("name", help="Element name")

    crawl_p = subparsers.add_parser("crawl", help="Combine two elements and crawl")
    crawl_p.add_argument("first", help="First element name")
    crawl_p.add_argument("second", help="Second element name")

    permute_p = subparsers.add_parser("permute", help="Combine all matching elements with each other")
    permute_p.add_argument("query", help=_QUERY_HELP)

    cross_p = subparsers.add_parser("cross", help="Cross-combine matches from two queries")
    cross_p.add_argument("left", help=_QUERY_HELP)
    cross_p.add_argument("right", help=_QUERY_HELP)

    with_p = subparsers.add_parser("with", help="Combine element with all matching discoveries")
    with_p.add_argument("element", help="Element name")
    with_p.add_argument("query", help=_QUERY_HELP)

    args = parser.parse_args()

    if args.command is None:
        asyncio.run(interactive_mode())
    else:
        asyncio.run(noninteractive_mode(args))


if __name__ == "__main__":
    main()
