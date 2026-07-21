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
