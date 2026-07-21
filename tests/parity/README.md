# Host parity lockstep

This harness drives the hand-written kernel-wiring glue in `cli.py` and
`trainer.src.mjs` (not the sudo-generated kernel itself) with identical
scenarios and diffs their canonicalized JSON output.

Kernel lockstep (`sudoc test --target py --target js sudo/craft.sudo`) never
touches either host's tuple/object conversion, status-int handling, or
exact-case name index. A wiring bug — not a kernel divergence — shows up here
as a scenario mismatch between the two hosts.

Run locally with `bash tests/parity/run_parity.sh` (regenerates `_sudo/`
adapters via `scripts/generate.sh` if missing).

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
| `classify` | `{"line": str}` | `[kind, payload] \| null` | **New.** Drives `_classify_command_line` (Python) directly on a bare command-line string; no elements/recipes involved. **JS-side (`run_js.mjs` / `trainer.src.mjs`) support is pending — another lane's task.** Until it lands, `run_parity.sh` will fail on `classify`/`validate` scenario ids with "unknown op" on the JS side; this is expected, not a regression. |
| `validate` | `{"line": str}` | `str \| null` | **New.** Drives `_validate_command_line` (Python) directly on a bare command-line string. Same JS-pending caveat as `classify` above. |
