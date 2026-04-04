#!/usr/bin/env python3
"""Infinite Craft CLI — combine elements from the terminal or as a scripted tool."""

import asyncio
import argparse
import fnmatch
import gzip
import json
import os
import readline  # noqa: F401 — enables arrow keys, history in input()
import signal
import sys
import time
import urllib.request

from infinitecraft import InfiniteCraft, Element

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
def _resolve_element(game, name: str):
    """Look up an element by name in discoveries; fall back to bare Element."""
    found = game.get_discovery(name)
    if found is not None:
        return found
    # Also try title-cased version
    title = name.strip().title()
    if title != name:
        found = game.get_discovery(title)
        if found is not None:
            return found
    return Element(name=name.strip().title())


# Runtime cache for pair results — avoids re-hitting the API for the same combo
_pair_cache: dict[tuple[str, str], Element] = {}


async def _cached_pair(game, a, b):
    """Wrapper around game.pair that caches results by sorted element names."""
    key = tuple(sorted([a.name, b.name]))
    if key in _pair_cache:
        return _pair_cache[key]
    result = await game.pair(a, b)
    _pair_cache[key] = result
    if result.name is not None:
        _record_recipe(result.name, a.name, b.name)
    return result


async def do_combine(game, first_name: str, second_name: str) -> str:
    first = _resolve_element(game, first_name)
    second = _resolve_element(game, second_name)
    try:
        result = await _cached_pair(game, first, second)
    except Exception as e:
        return _color(f"  Error: {e}", RED)
    # If the pairing succeeded, ensure both inputs are in discoveries too
    if result.name is not None:
        for elem in (first, second):
            game._update_discoveries(
                name=elem.name, emoji=elem.emoji, is_first_discovery=False
            )
    result_display = result.name if result.name else "Nothing"
    _history.append((first_name.strip(), second_name.strip(), result_display))
    return format_result(str(first), str(second), result)


def _match_elements(game, query: str) -> list:
    """Return discovered elements matching a query.

    Supports wildcards (* and ?) via fnmatch when present,
    otherwise falls back to substring matching.
    Prefix with ^ to limit to first discoveries only.
    """
    discoveries = game.get_discoveries()
    q = query.strip()
    only_new = q.startswith("^")
    if only_new:
        q = q[1:]
    q = q.lower()
    if any(c in q for c in "*?[]"):
        matches = [e for e in discoveries if fnmatch.fnmatch(e.name.lower(), q)]
    else:
        matches = [e for e in discoveries if q in e.name.lower()]
    if only_new:
        matches = [e for e in matches if e.is_first_discovery]
    return matches


def do_search(game, query: str) -> str:
    matches = _match_elements(game, query)
    if not matches:
        return "  No matches found."
    return "\n".join(f"  {format_element(e)}" for e in matches)


def do_recipe(game, name: str) -> str:
    """Show shortest recipe tree for an element via BFS on local recipes."""
    recipes = _load_recipes()
    target = name.strip()

    # Find exact match in discoveries
    elem = game.get_discovery(target)
    if elem is None:
        elem = game.get_discovery(target.title())
    if elem is None:
        return f"  {target} not found in discoveries."
    target = elem.name

    if target in _BASE_ELEMENTS:
        return f"  {target} is a base element."

    if target not in recipes:
        return f"  No recipe known for {target}. Try /fill or /import."

    # BFS to find all elements needed, tracking shortest path
    # parent[name] = (a_name, b_name) that produces it
    parent = {}
    visited = set(_BASE_ELEMENTS)
    found = False

    while not found:
        # Find everything we can make using ONLY previously visited elements
        new_this_layer = {}
        for result_name, pairs in recipes.items():
            if result_name in visited or result_name in new_this_layer:
                continue
            for pair in pairs:
                if pair[0] in visited and pair[1] in visited:
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

    # Walk back from target to collect steps in order
    steps = []
    to_resolve = [target]
    resolved = set(_BASE_ELEMENTS)
    while to_resolve:
        name = to_resolve.pop()
        if name in resolved:
            continue
        a, b = parent[name]
        # Ensure dependencies are resolved first
        if a not in resolved:
            to_resolve.append(name)  # re-queue
            to_resolve.append(a)
            continue
        if b not in resolved:
            to_resolve.append(name)  # re-queue
            to_resolve.append(b)
            continue
        steps.append((a, b, name))
        resolved.add(name)

    lines = [f"  Recipe for {_color(target, BOLD)} ({len(steps)} steps):"]
    for a, b, r in steps:
        a_elem = game.get_discovery(a)
        b_elem = game.get_discovery(b)
        r_elem = game.get_discovery(r)
        a_str = str(a_elem) if a_elem else a
        b_str = str(b_elem) if b_elem else b
        r_str = format_element(r_elem) if r_elem else r
        lines.append(f"    {a_str} + {b_str} = {r_str}")
    return "\n".join(lines)


def do_list(game) -> str:
    discoveries = game.get_discoveries()
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


async def do_crawl(game, first_name: str, second_name: str):
    """Combine two elements, then iteratively combine results with all inputs until nothing new."""
    first = _resolve_element(game, first_name)
    second = _resolve_element(game, second_name)
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
        await _combine_pairs(game, new_pairs)

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


async def do_exhaust(game, name: str):
    """Combine an element with every discovered element."""
    target = _resolve_element(game, name)
    others = list(game.get_discoveries())
    pairs = [(target, o) for o in others if o.name != target.name]
    print(f"  Combining {_color(str(target), BOLD)} with {len(pairs)} elements...")
    await _confirm_and_run_pairs(game, pairs)


_BULK_WARN_THRESHOLD = 200


_cancelled = False


async def _combine_pairs(game, pairs: list[tuple]):
    """Combine a list of (element, element) pairs with light parallelism."""
    global _cancelled
    _cancelled = False
    loop = asyncio.get_event_loop()
    original_handler = None

    def on_sigint():
        global _cancelled
        _cancelled = True

    # Install SIGINT handler that sets flag instead of raising
    import signal
    try:
        original_handler = loop.add_signal_handler(signal.SIGINT, on_sigint)
    except NotImplementedError:
        pass  # Windows

    total = len(pairs)
    new_count = 0
    nothing_count = 0
    done_count = 0
    known_names = {e.name for e in game.get_discoveries()}
    sem = asyncio.Semaphore(API_CONCURRENCY)
    lock = asyncio.Lock()

    async def process(a, b):
        nonlocal new_count, nothing_count, done_count
        try:
            result = await _cached_pair(game, a, b)
        except Exception as e:
            done_count += 1
            print(f"  [{done_count}/{total}] {a} + {b} = {_color(f'Error: {e}', RED)}")
            return
        done_count += 1
        if result.name is not None:
            for elem in (a, b):
                game._update_discoveries(
                    name=elem.name, emoji=elem.emoji, is_first_discovery=False
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


async def _confirm_and_run_pairs(game, pairs: list[tuple]):
    """Warn if too many pairs, then run them."""
    if len(pairs) > _BULK_WARN_THRESHOLD:
        print(f"\n  {_color(f'Warning: this will make {len(pairs)} API requests.', YELLOW)}")
        try:
            answer = input("  Continue? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  Cancelled.")
            return
        if answer not in ("y", "yes"):
            print("  Cancelled.")
            return
    print()
    await _combine_pairs(game, pairs)


async def do_permute(game, query: str):
    """Combine every pair of elements matching the query with each other."""
    matches = _match_elements(game, query)
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
    await _confirm_and_run_pairs(game, pairs)


async def do_cross(game, left_query: str, right_query: str):
    """Cross-combine all elements matching left_query with all matching right_query."""
    left = _match_elements(game, left_query)
    right = _match_elements(game, right_query)
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
    await _confirm_and_run_pairs(game, pairs)


# ---------------------------------------------------------------------------
# Infinibrowser integration
# ---------------------------------------------------------------------------
_IB_BASE = "https://infinibrowser.wiki/api"
_IB_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


_ib_cache: dict[str, dict | None] = {}


def _ib_request(path: str, params: dict) -> dict | None:
    """Raw Infinibrowser fetch with caching. Returns parsed JSON or None on error."""
    qs = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items())
    cache_key = f"{path}?{qs}"
    if cache_key in _ib_cache:
        return _ib_cache[cache_key]
    url = f"{_IB_BASE}/{path}?{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": _IB_UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
    except Exception:
        return None  # don't cache errors
    _ib_cache[cache_key] = result
    return result


def _ib_fetch(path: str, params: dict) -> dict | None:
    """Fetch from the Infinibrowser API. Prints errors."""
    result = _ib_request(path, params)
    if result is None:
        print(f"  {_color('Infinibrowser request failed', RED)}")
    return result


def _ib_fetch_quiet(path: str, params: dict) -> dict | None:
    """Fetch from the Infinibrowser API. Silent on errors."""
    return _ib_request(path, params)


def _refresh_discoveries(game):
    """Reload in-memory discoveries from disk."""
    raw = game._get_raw_discoveries()
    game._discoveries = [
        Element(name=d["name"], emoji=d.get("emoji"), is_first_discovery=d.get("is_first_discovery"))
        for d in raw
    ]
    game.discoveries = game._discoveries.copy()


def _import_from_infinibrowser(game, name: str) -> str:
    """Look up an element on Infinibrowser, show its lineage, and import into discoveries."""
    data = _ib_fetch("item", {"id": name})
    if data is None:
        return ""
    if "code" in data:
        return f"  {_color('Not found', DIM)} on Infinibrowser: {name}"

    emoji = data.get("emoji", "")
    depth = data.get("depth", "?")
    print(f"  Found: {emoji} {data['text']}  (depth {depth})")

    lineage = _ib_fetch("recipe", {"id": name})
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
                game._update_discoveries(
                    name=elem_name, emoji=elem_emoji, is_first_discovery=False
                )
                imported.add(elem_name)

    _refresh_discoveries(game)
    return f"  Imported {_color(str(len(imported)), GREEN)} elements into discoveries."


def _import_from_save(game, path: str) -> str:
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
        result = game._update_discoveries(
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

    _refresh_discoveries(game)
    total = len(items)
    return (f"  Loaded {_color(str(total), GREEN)} elements "
            f"({imported_count} new) with {recipe_count} recipes from {_color(path, BOLD)}")


def do_import(game, arg: str) -> str:
    """Import from Infinibrowser (element name) or .ic save file (path)."""
    if arg.endswith(".ic") or os.path.sep in arg:
        return _import_from_save(game, arg)
    return _import_from_infinibrowser(game, arg)


_BASE_ELEMENTS = {"Water", "Fire", "Wind", "Earth"}


def _fill_missing_recipes(game):
    """Fetch lineages from Infinibrowser for elements missing recipes.

    When a lineage is fetched, its intermediate elements get recipes too,
    so we re-check the missing set after each fetch to skip already-filled items.
    """
    recipes = _load_recipes()
    name_set = {e.name for e in game.get_discoveries()}
    missing = {e.name for e in game.get_discoveries()
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
                        game._update_discoveries(
                            name=elem_name, emoji=elem_emoji, is_first_discovery=False
                        )
                        name_set.add(elem_name)
            fetched += 1
            time.sleep(0.5)  # rate limit Infinibrowser
    except KeyboardInterrupt:
        print(f"\n  Stopped early.")
    _refresh_discoveries(game)
    print(f"\n  Fetched {fetched} lineages, {skipped} already filled by prior lineages.", end="")
    if failed:
        print(f" {_color(str(len(failed)), YELLOW)} not found on Infinibrowser.")
    else:
        print()


def do_unfilled(game) -> str:
    """List elements that have no recipes (excluding base elements)."""
    recipes = _load_recipes()
    discoveries = game.get_discoveries()
    missing = [e for e in discoveries if e.name not in _BASE_ELEMENTS and e.name not in recipes]
    if not missing:
        return "  All elements have recipes."
    lines = [f"  {len(missing)} elements without recipes:\n"]
    for e in missing:
        lines.append(f"    {format_element(e)}")
    return "\n".join(lines)


def do_export(game, path: str = EXPORT_PATH) -> str:
    """Export discoveries to an Infinite Craft .ic save file.

    Only includes elements that have recipes or are base elements.
    Use /fill first to fetch missing recipes from Infinibrowser.
    """
    recipes = _load_recipes()
    discoveries = game.get_discoveries()

    # Build export, only including elements that have recipes or are base
    name_to_id = {}
    items = []
    idx = 0
    for elem in discoveries:
        if elem.name not in _BASE_ELEMENTS and elem.name not in recipes:
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
        msg += f"\n  {_color(str(excluded), YELLOW)} elements excluded (no recipes — use /fill to fetch them)"
    return msg


def do_help() -> str:
    return """  Commands:
    <element> + <element>     Combine two elements
    <element> ++ <element>    Combine & crawl: iterate until no new discoveries
    <element> + | <query>     Combine element with all matching discoveries
    <query> * <query>         Cross-combine all matches from both queries
    /search <query>           Search discoveries (supports * ? wildcards, ^ for new)
    /recipe <element>         Show shortest recipe from base elements
    /list                     List all discovered elements
    /exhaust <element>        Combine element with all discoveries
    /crawl <el> + <el>        Same as ++ (alternate syntax)
    /permute <query>          Combine all matching elements with each other
    /import <element|file.ic> Import from Infinibrowser or .ic save file
    /fill                     Fetch missing recipes from Infinibrowser
    /unfilled                 List elements without recipes
    /export [path]            Export discoveries as .ic save file
    /history                  Show combinations tried this session
    /help                     Show this help
    /quit                     Exit"""


# ---------------------------------------------------------------------------
# Interactive REPL
# ---------------------------------------------------------------------------
async def interactive_mode():
    print(_color("=== Infinite Craft CLI ===", BOLD + CYAN))
    print()

    async with InfiniteCraft(discoveries_storage=DISCOVERIES_PATH, api_rate_limit=API_RATE_LIMIT) as game:
        starters = "  ".join(str(e) for e in game.discoveries[:4])
        print(f"  Starting elements: {starters}")
        total = len(game.get_discoveries())
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
            elif line.startswith("/search"):
                query = line[len("/search"):].strip()
                if not query:
                    print("  Usage: /search <query>")
                else:
                    print(do_search(game, query))
            elif line.startswith("/recipe"):
                name = line[len("/recipe"):].strip()
                if not name:
                    print("  Usage: /recipe <element>")
                else:
                    print(do_recipe(game, name))
            elif line == "/list":
                print(do_list(game))
            elif line.startswith("/permute"):
                query = line[len("/permute"):].strip()
                if not query:
                    print("  Usage: /permute <query>")
                else:
                    await do_permute(game, query)
            elif line.startswith("/import"):
                name = line[len("/import"):].strip()
                if not name:
                    print("  Usage: /import <element>")
                else:
                    print(do_import(game, name))
            elif line.startswith("/unfilled"):
                print(do_unfilled(game))
            elif line.startswith("/fill"):
                _fill_missing_recipes(game)
            elif line.startswith("/export"):
                path = line[len("/export"):].strip() or EXPORT_PATH
                print(do_export(game, path))
            elif line.startswith("/exhaust"):
                name = line[len("/exhaust"):].strip()
                if not name:
                    print("  Usage: /exhaust <element>")
                else:
                    await do_exhaust(game, name)
            elif line.startswith("/crawl"):
                rest = line[len("/crawl"):].strip()
                if "+" not in rest:
                    print("  Usage: /crawl <element> + <element>")
                else:
                    parts = rest.split("+", 1)
                    first, second = parts[0].strip(), parts[1].strip()
                    if not first or not second:
                        print("  Usage: /crawl <element> + <element>")
                    else:
                        await do_crawl(game, first, second)
            elif line == "/history":
                print(do_history())
            elif "++" in line:
                parts = line.split("++", 1)
                first = parts[0].strip()
                second = parts[1].strip()
                if not first or not second:
                    print("  Usage: <element> ++ <element>")
                else:
                    await do_crawl(game, first, second)
            elif "+|" in line:
                # element + | query
                parts = line.split("+|", 1)
                name = parts[0].strip()
                query = parts[1].strip()
                if not name or not query:
                    print("  Usage: <element> + | <query>")
                else:
                    target = _resolve_element(game, name)
                    others = _match_elements(game, query)
                    if not others:
                        print(f"  No elements match: {query}")
                    else:
                        pairs = [(target, o) for o in others if o.name != target.name]
                        print(f"  Combining {_color(str(target), BOLD)} with {len(pairs)} elements matching {_color(query, YELLOW)}...")
                        await _confirm_and_run_pairs(game, pairs)
            elif " * " in line:
                # query * query
                parts = line.split(" * ", 1)
                left_q = parts[0].strip()
                right_q = parts[1].strip()
                if not left_q or not right_q:
                    print("  Usage: <query> * <query>")
                else:
                    await do_cross(game, left_q, right_q)
            elif "+" in line:
                parts = line.split("+", 1)
                first = parts[0].strip()
                second = parts[1].strip()
                if not first or not second:
                    print("  Usage: <element> + <element>")
                else:
                    print(await do_combine(game, first, second))
            else:
                print(f"  Unknown input. Type {_color('/help', YELLOW)} for commands.")


# ---------------------------------------------------------------------------
# Non-interactive CLI
# ---------------------------------------------------------------------------
async def noninteractive_mode(args):
    async with InfiniteCraft(discoveries_storage=DISCOVERIES_PATH, api_rate_limit=API_RATE_LIMIT) as game:
        if args.command == "combine":
            print(await do_combine(game, args.first, args.second))
        elif args.command == "search":
            print(do_search(game, args.query))
        elif args.command == "list":
            print(do_list(game))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Infinite Craft CLI")
    subparsers = parser.add_subparsers(dest="command")

    combine_p = subparsers.add_parser("combine", help="Combine two elements")
    combine_p.add_argument("first", help="First element name")
    combine_p.add_argument("second", help="Second element name")

    search_p = subparsers.add_parser("search", help="Search discovered elements")
    search_p.add_argument("query", help="Search substring")

    subparsers.add_parser("list", help="List all discovered elements")

    args = parser.parse_args()

    if args.command is None:
        asyncio.run(interactive_mode())
    else:
        asyncio.run(noninteractive_mode(args))


if __name__ == "__main__":
    main()
