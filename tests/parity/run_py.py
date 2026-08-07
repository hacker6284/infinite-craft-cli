#!/usr/bin/env python3
"""Host-parity runner: drive Python wiring (cli.py) against fixtures.json."""

from __future__ import annotations

import json
import os
import sys
import tempfile

import infinite_craft_cli.cli as cli
from infinite_craft_cli.cli import (
    _export_included,
    _load_recipes,
    _match_elements,
    _record_recipe,
    _resolve_element,
    _trace_recipe,
)
from infinite_craft_cli.storage import DiscoveryStorage

FIXTURES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures.json")


def _write_discoveries(path: str, elements: list) -> None:
    payload = [
        {"name": n, "emoji": e, "is_first_discovery": bool(f)}
        for n, e, f in elements
    ]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def _resolve_elements_and_recipes(scenario: dict, fixtures: dict):
    """Return (elements, recipes) for a scenario."""
    if "chain" in scenario:
        n = scenario["chain"]
        elements = [("Fire", "", False), ("Water", "", False)] + [
            (f"C{i}", "", False) for i in range(0, n + 1)
        ]
        recipes = {"C0": [["Fire", "Water"]]}
        for i in range(1, n + 1):
            recipes[f"C{i}"] = [[f"C{i - 1}", "Water"]]
        return elements, recipes

    if "elements_set" in scenario:
        elements = fixtures["element_sets"][scenario["elements_set"]]
    elif "elements" in scenario:
        elements = scenario["elements"]
    else:
        elements = []

    recipes = scenario.get("recipes") or scenario.get("initial_recipes") or {}
    return elements, recipes


def _run_scenario(scenario: dict, fixtures: dict, scratch_root: str):
    op = scenario["op"]
    sid = scenario["id"]
    scratch = os.path.join(scratch_root, sid)
    os.makedirs(scratch, exist_ok=True)

    discoveries_path = os.path.join(scratch, "discoveries.json")
    recipes_path = os.path.join(scratch, "recipes.json")

    elements, recipes = _resolve_elements_and_recipes(scenario, fixtures)

    if op == "record_recipe":
        elements = []
        recipes = scenario.get("initial_recipes") or {}

    _write_discoveries(discoveries_path, elements)
    with open(recipes_path, "w", encoding="utf-8") as f:
        json.dump(recipes, f)

    old_recipes_path = cli.RECIPES_PATH
    cli.RECIPES_PATH = recipes_path
    try:
        storage = DiscoveryStorage(discoveries_path)

        if op == "match":
            elems, _err = _match_elements(storage, scenario["query"])
            return [
                [e.name, e.emoji or "", bool(e.is_first_discovery)] for e in elems
            ]

        if op == "resolve":
            e = _resolve_element(storage, scenario["name"])
            return [e.name, e.emoji or "", bool(e.is_first_discovery)]

        if op == "record_recipe":
            for call in scenario["calls"]:
                _record_recipe(call["result"], call["a"], call["b"])
            loaded = _load_recipes()
            return {
                k: sorted([list(p) for p in v]) for k, v in loaded.items()
            }

        if op == "trace":
            status, target, steps = _trace_recipe(storage, scenario["name"])
            return {
                "status": status,
                "target": target,
                "steps": [[a, b, r] for a, b, r in steps],
            }

        if op == "export":
            included = _export_included(storage)
            return sorted(
                ([n, em, bool(f)] for n, em, f in included),
                key=lambda t: t[0],
            )

        if op == "classify":
            result = cli._classify_command_line(scenario["line"])
            return list(result) if result is not None else None

        if op == "validate":
            return cli._validate_command_line(scenario["line"])

        if op == "operands":
            result = cli.craft.parse_operands(scenario["kind"], scenario["payload"])
            return list(result) if result is not None else None

        if op == "permute_pairs":
            raw = cli.craft.permute_pairs_boundary(scenario["matches"])
            return [list(t) for t in raw]

        if op == "cross_pairs":
            raw = cli.craft.cross_pairs_boundary(scenario["left"], scenario["right"])
            return [list(t) for t in raw]

        if op == "with_pairs":
            raw = cli.craft.with_pairs_boundary(scenario["target"], scenario["others"])
            return [list(t) for t in raw]

        if op == "unfilled":
            return list(
                cli.craft.unfilled_names_boundary(elements, recipes)
            )

        if op == "sanitize":
            return cli.craft.sanitize_element_name(scenario["name"])

        if op == "ic_batches":
            els, recs = cli.craft.ic_save_to_batches(
                scenario["items"], scenario.get("recipe_refs", [])
            )
            return {
                "elements": [list(t) for t in els],
                "recipes": [list(t) for t in recs],
            }

        if op == "lineage_batches":
            els, recs = cli.craft.lineage_steps_to_batches(scenario["steps"])
            return {
                "elements": [list(t) for t in els],
                "recipes": [list(t) for t in recs],
            }

        if op == "export_items":
            items, refs = cli.craft.build_export_items_boundary(elements, recipes)
            return {
                "items": [list(t) for t in items],
                "refs": [list(t) for t in refs],
            }

        if op == "crawl_pairs":
            raw_pairs, new_keys = cli.craft.crawl_generation_pairs_boundary(
                scenario["pool"], scenario.get("tried_keys", [])
            )
            return {
                "pairs": [list(t) for t in raw_pairs],
                "new_keys": list(new_keys),
            }

        if op == "validate_segments":
            segs = cli.craft.validate_command_line_segments(scenario["line"])
            if segs is None:
                return None
            return [[text, bool(hl)] for text, hl in segs]

        raise ValueError(f"unknown op: {op!r}")
    finally:
        cli.RECIPES_PATH = old_recipes_path


def run_all_scenarios(fixtures_path: str = FIXTURES_PATH) -> dict:
    """Run every scenario in fixtures_path against Python host wiring
    (cli.py) and return {scenario_id: result} for all of them.

    Raises RuntimeError (chained from the original exception) on the first
    scenario that errors — callers that want partial results or different
    error handling should catch RuntimeError, not rely on partial dict
    contents.
    """
    with open(fixtures_path, encoding="utf-8") as f:
        fixtures = json.load(f)
    results = {}
    with tempfile.TemporaryDirectory(prefix="parity-py-") as scratch_root:
        for scenario in fixtures["scenarios"]:
            sid = scenario["id"]
            try:
                results[sid] = _run_scenario(scenario, fixtures, scratch_root)
            except Exception as exc:
                raise RuntimeError(f"ERROR in scenario {sid!r}: {exc}") from exc
    return results


def main() -> int:
    try:
        results = run_all_scenarios()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(results, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
