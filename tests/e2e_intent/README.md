# Owner-intent E2E suite

Oracle = the owner's stated intent from the 2026-07-22 query-semantics grilling (seven numbered rulings R1–R7, plus unnumbered "pin" scenarios), encoded by hand into `intent_fixtures.json` and checked against the real `infinite_craft_cli.cli` functions the REPL dispatches to.

**vs `tests/parity/`:** that suite is host-vs-host (Python `cli.py` wiring vs JS `trainer.src.mjs` wiring). It only asserts the hosts agree; it has no opinion on owner intent. This suite checks a single host (Python) against intent, not against the other host.

**vs the rest of `tests/`:** unit suites are code-derived — written by reading the implementation and pinning current behavior. These fixtures come from the ruling text itself. A fixture that describes correct owner intent the kernel does not yet implement is marked `"status": "pending-v0.2.0"` and appears as a skipped test (not a failure, not silently absent) until a future sudoc compiler version lands the missing regex feature.

**Add a ruling:** append one object to the `"scenarios"` array in `intent_fixtures.json` (reuse an `"elements"` set from `"element_sets"` when possible, or add a new named set), set `"ruling"` and a human-readable `"note"`. No change to `intent_test.py` unless you need a new `"surface"` beyond `"search"` / `"validate"`.
