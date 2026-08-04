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
