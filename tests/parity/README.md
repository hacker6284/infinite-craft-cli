# Host parity lockstep

This harness drives the hand-written kernel-wiring glue in `cli.py` and
`trainer.src.mjs` (not the sudo-generated kernel itself) with identical
scenarios and diffs their canonicalized JSON output.

Kernel lockstep (`sudoc test --target py --target js sudo/craft.sudo`) never
touches either host's tuple/object conversion, status-int handling, or
exact-case name index. A wiring bug — not a kernel divergence — shows up here
as a scenario mismatch between the two hosts.

Run locally with `bash tests/parity/run_parity.sh` (wraps
`bazel test //tests/parity:parity_test`; the `_sudo/` adapters are built by
Bazel, no separate regeneration step needed).

`fixtures.json` is data-only: add a scenario by adding one JSON object; no
code changes needed in most cases.

## Scenario types

| `op` | Shape | Result shape | Notes |
|---|---|---|---|
| `match` | `{"query": str}` + one of `elements_set`/`elements`/`chain` | `[[name, emoji, first], ...]` | Now covers real `\|` alternation, `\d`-style backslash escapes, and the `!`/bare-`!` exclude filter (DIVERGENCES.md ruling 7) as well as plain substring/glob queries. |
| `resolve` | `{"name": str}` + elements | `[name, emoji, first]` | |
| `record_recipe` | `{"initial_recipes": {...}, "calls": [{"result","a","b"}, ...]}` | `{result: [[a,b], ...]}` | |
| `trace` | `{"name": str}` + elements/recipes/`chain` | `{"status", "target", "steps"}` | |
| `export` | elements/recipes | `[[name, emoji, first], ...]` (sorted by name) | |
| `validate` | `{"line": str}` | `str \| null` | **New.** Drives `_validate_command_line` (Python) directly on a bare command-line string. Same JS-pending caveat as `classify` above. |
| `operands` | `{"kind": str, "payload": str}` | `[left, right] \| null` | Drives `parse_operands` through each host's kernel adapter — shorthand operand extraction (`++`, `+\|`, `*`, `+`), Python maxsplit semantics. |
| `permute_pairs` | `{"matches": [[name, emoji, first], ...]}` | `[[an, ae, af, bn, be, bf], ...]` | Upper-triangle pair generation via each host's kernel adapter. |
| `cross_pairs` | `{"left": [...], "right": [...]}` | same 6-tuple list | Left-major product, same-name skip, symmetric pair-key dedup. |
| `with_pairs` | `{"target": [name, emoji, first], "others": [...]}` | same 6-tuple list | Target × others, self skipped. |
| `unfilled` | elements + recipes | `[name, ...]` | Elements with no known recipe (empty list = unfilled; bases never unfilled), storage order. |
| `validate_segments` | `{"line": str}` | `[[text, highlight], ...] \| null` | Structured validation-error segments — message text plus highlight flags; hosts only style (ANSI vs HTML span). |
| `crawl_pairs` | `{"pool": [...], "tried_keys": [...]}` | `{"pairs": [...], "new_keys": [...]}` | One crawl generation: sorted-name pool order, self-pairs included, untried only; keys are kernel-encoded. |
| `sanitize` | `{"name": str}` | `str` | Storage name normalization: strip + drop C0/C1/DEL and U+2028/9. |
| `ic_batches` | `{"items": [[id, text, emoji, disc], ...], "recipe_refs": [[rid, aid, bid], ...]}` | `{"elements": [...], "recipes": [...]}` | .ic import fold: id resolution, sanitization, dangling refs dropped. |
| `lineage_batches` | `{"steps": [[a, a_emoji, b, b_emoji, r, r_emoji], ...]}` | `{"elements": [...], "recipes": [...]}` | Infinibrowser lineage fold: sanitized, deduped elements; malformed steps skipped. |
| `export_items` | elements + recipes | `{"items": [[id, text, emoji, first], ...], "refs": [[rid, aid, bid], ...]}` | .ic export builder: fresh sequential ids over the closure, recipes remapped, no dangling ids. |
| `prioritize_pairs` | `{"pairs": [6-tuples], "recipes": {...}}` | same 6-tuple list, reordered | Batch execution order: ingredient-usage score (sum) descending, ascending pair-key tie-break. |
