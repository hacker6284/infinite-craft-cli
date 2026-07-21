# Changelog

## [Unreleased]

### Extension loader hardening (was drafted as 1.3.1)

### Fixed
- Chrome extension loader failed on neal.fun because inline `script.textContent` injection is blocked by the site's Content Security Policy. Added a tiny extension-origin `page-bridge.js` that loads via `chrome.runtime.getURL` (CSP-exempt) and executes the fetched trainer with `eval` in the page world so IndexedDB access still works.

### Extension fetches hosted trainer (was drafted as 1.3.0)

### Changed
- Chrome extension is now a thin loader that fetches `trainer.min.js` from GitHub Pages and injects it into the page context, instead of bundling a local copy of `trainer.js`. Trainer updates ship via the hosted bookmarklet without requiring a Chrome Web Store release. Removed `web_accessible_resources` for the bundled trainer; added `host_permissions` for `hacker6284.github.io`. Loader hardening: fetch timeout, bounded retries with backoff, payload validation (size, Content-Type, sentinel), injection error handling, UI init verification, `cache: 'no-store'`. Regenerated `trainer.min.js` from current `trainer.js`; CI test prevents minified artifact drift. Userscript aligned to `trainer.min.js`. Extension manifest version `1.3.0` matches changelog.

## [1.4.2] - 2026-06-22

### Fixed
- Eliminated the last remaining brittleness in the high-level test framework. The `test_streaming_bulk_slow_pairs_interleaved_local_and_queue_status_via_harness` test (and similar) no longer depended on fragile global `rfind` position ordering between output lines and chrome/queue status redraws. Replaced with suffix checks after specific output markers + explicit yields to guarantee interleaving. All `TestREPLHarnessEdges` tests remain strictly behavioral using only `in`/`rfind`, `prompt_calls[-1]`, `Events`, and capsys — no `cli._*` access, no counts, no exact string matches.

## [1.4.1] - 2026-06-22

### Fixed
- CI (Bazel) tests now pass: shared test utilities (`tests/help_utils.py`, legacy runner helpers) are properly declared as `py_library` and wired as dependencies for all `py_test` targets. This unblocks publishing after the v1.4.0 tag.
- Moved `_run_interactive` / `run_async` helpers to `help_utils` to support cross-test imports under Bazel's hermetic runfiles without breaking legacy direct-drive tests.

## [1.4.0] - 2026-06-22

### Added
- `/exhaust <query>` — each element matching the query is combined with all discoveries (generalizes single-element exhaust)
- `/permutate <query>` — repeatedly runs `/permute` until a round produces no new discoveries
- Command queue for long-running API commands in the Python REPL and browser trainers: local commands (`/help`, `/search`, `/list`, `/recipe`, `/history`, `/clear`, `/unfilled`, `/queue`) run immediately; other commands queue FIFO with queue displayed above the prompt

### Changed
- **Breaking:** `!<query>` now excludes matching elements; `^<query>` filters to first discoveries only (previously both `!` and `^` meant first discoveries; delimited regex `/^fi/` unchanged)
- `exhaust` CLI subcommand now takes a query argument instead of an element name

### Fixed
- `/queue` with TTY chrome now prints scroll-area status (line-by-line) instead of appearing to do nothing
- Esc skip during rate-limit acquire/backoff waits is now responsive (~50ms polling) instead of blocking until the window expires
- Python REPL queue UX: bordered status panel above the prompt (running + numbered pending), `Queued:` acknowledgment when deferred, `[N active]` prompt hint, and TTY in-place panel clear when idle (no stale `Running:` lines)
- `/fill`, `/prune`, and Infinibrowser `/import` no longer block the REPL event loop (HTTP and rate-limit sleeps run via `asyncio.to_thread` / `await asyncio.sleep`); local commands stay responsive during queued fill/prune/import work
- Ctrl+C during a queued command now discards remaining queue items instead of continuing to the next one
- Bulk confirmation (`y`/`n`) no longer gets mis-queued when typed before `Continue? [y/N]` appears; early answers are buffered and the prompt switches to `confirm [y/N]>`
- `/queue` shows queue status (local, immediate); unknown `/commands` are rejected instead of being enqueued; deferring a command while another runs prints `Queued:`
- Trainer scroll wheel now works over the trainer GUI, not only over the element library
- Stop button reliably cancels in-progress commands, including during bulk confirmation prompts
- `recipes.json` and `discoveries.json` now save atomically (temp file + `os.replace`) so interrupted writes cannot truncate the file
- Corrupt `recipes.json` / `discoveries.json` surfaces a clear repair message (`RecipeStoreError` / `ValueError`) instead of a raw `JSONDecodeError` during bulk combines

## [1.3.0] - 2026-06-10

### Added
- REPL slash commands `/combine`, `/with`, and `/cross` mirroring `+`, `+|`, and `*` shorthands
- Non-interactive `with` subcommand: `infinite-craft with <element> <query>`
- Regex query syntax via `/pattern/` delimiters (case-insensitive)
- `!` prefix for first-discovery filters (`^` retained as legacy alias)
- Reorganized `/help` with shorthand/slash-command groupings and query-syntax documentation
- `regex` package dependency for bounded-time regex matching (20ms timeout)

### Changed
- Browser extension and bookmarklet trainers brought to parity with the Python CLI: `/combine`, `/with`, `/cross`, grouped `/help`, `!`/`/pattern/` query syntax, spaced operator delimiters, and matching parser/dispatch behavior
- Regenerated `bookmarklet/trainer.min.js`; updated `bookmarklet/index.html` command reference

### Fixed
- Empty regex `//` and empty queries after `!`/`^` no longer match all elements
- `do_with()` and `do_exhaust()` short-circuit when no valid pairs remain
- `/cross` delimited-regex queries with spaces require explicit ` * ` delimiter; substring queries with `/` still work
- Element names containing `+` or `++` no longer misfire combine/crawl parsers (combine requires spaced ` + `)
- `/with`, `/cross`, `/fill`, `/unfilled`, `/prune` no longer misroute similarly-prefixed commands
- Invalid regex patterns report distinct errors ("Invalid regex pattern" vs "Regex pattern too complex")
- ReDoS mitigation: nested-quantifier and alternation-quantifier rejection, regex body length cap, 20ms timeout

## [1.2.9] - 2026-06-07

### Fixed
- Browser trainers no longer wedge after long `/exhaust`, `/crawl`, `/cross`, `/fill`, `/prune`, or other bulk commands when `running` fails to reset, the stop button is used, or confirmation is abandoned. Centralized `beginRun()`/`endRun()` helpers guarantee `running=false` before UI cleanup; stop now clears `waitingForConfirm`; `dispatch()` shows a busy message instead of silently ignoring input; bulk confirm sets `running` before `waitForInput` to block double-dispatch. (bookmarklet/trainer.js + extension/trainer.js)

## [1.2.8] - 2026-06-07

### Added
- `/prune` command in the Python CLI and browser trainers: removes orphan discoveries (no recipe lineage) that Infinibrowser confirms cannot be filled. API errors skip elements rather than deleting them.

### Fixed
- Browser trainers no longer add combine operands to discoveries when a pairing returns Nothing (`doCombine` and crawl initial pair). Operands are persisted only on success, matching the Python CLI.

## [1.2.7] - 2026-06-05

### Fixed
- Trainer (browser overlay) CLI no longer becomes permanently wedged after `/crawl`, `/exhaust`, `/permute`, `/cross`, `/fill`, or other bulk commands (previously required page refresh to recover commands). The `running` flag and stop button visibility are now guaranteed to reset via `try`/`finally` in `runPairs`, `doCrawl`, and `doFill` on success, error, early return, and cancel paths. Added defensive `.catch()` on `dispatch()` from the keydown handler as last-ditch un-wedge. (bookmarklet/trainer.js + extension/trainer.js; Python CLI unaffected.)

## [1.2.6] - 2026-05-19

### Fixed
- `/recipe` (and the equivalent in the browser trainers) can now trace lineages fetched by `/fill` / `/import` even when a constituent element has no recipe of its own (terminal leaves from Infinibrowser). The BFS now treats such terminals as additional roots, so "Cannot trace full lineage" is no longer incorrectly emitted for valid filled recipes.
- `do_export` now includes elements that are referenced by filled recipes (even if they lack their own recipes) so that the recipe pairs survive the export/import round-trip. Pure orphans unrelated to any recipe are still excluded.

### Improved (from code review)
- Simplified redundant predicate in the internal availability check for recipe constituents and strengthened the "no recipe known" guard for targets with empty recipe lists (for consistency with the JS trainers and the terminal concept).
- Added a regression test exercising that unresolvable middles (names that have a non-empty recipe entry but lead to dead-ends that are not terminals) still correctly produce "Cannot trace full lineage".
- Tightened a test assertion, improved comments around terminal handling and base pre-resolution, added cross-file sync notes to the duplicated JS recipe logic, and clarified the excluded-elements message in export to match the updated closure semantics.

## [1.2.5] - 2026-05-18

### Fixed
- Replaced deprecated `asyncio.get_event_loop()` (inside async context) with `asyncio.get_running_loop()` in bulk pair processing. Avoids future RuntimeError / warnings on newer Python versions.

## [1.2.4] - 2026-05-18

### Fixed
- Race condition in `RateLimiter` when multiple concurrent `acquire()` calls occur (e.g. during `/crawl`, `/exhaust`, bulk combine with `API_CONCURRENCY=2`). Could previously exceed the rate limit and trigger Cloudflare blocks. Now properly serialized with `asyncio.Lock`.

## [1.2.3] - 2026-05-18

### Fixed
- Test collection failures with `pytest` (and `uv run pytest`) in environments that have third-party packages installing a conflicting top-level `tests` package (e.g. g2pkk). Added `tests/__init__.py` so the local test package takes precedence.
- `--version` flag (and `infinite_craft_cli.__version__`) always reported the stale hardcoded "1.0.0". Now dynamically loads the real version from package metadata so it matches the current git tag / PyPI release.

## [1.2.2] - 2026-04-06

### Fixed
- `/import` no longer fails when a stale cached empty recipe exists from earlier in the session.

### Changed
- Recipe fetches in `/import` now bypass the sync cache to always get fresh data from Infinibrowser.

## [1.2.1] - 2026-04-06

### Fixed
- Combined elements now persist to discoveries — previously only the inputs were saved, not the result.

### Added
- 29 new tests (163 → 192 total), including:
  - E2E regression tests with real storage (combine→persist→reload, export→import round-trip, recipe integration).
  - Interactive mode command parsing and dispatch tests.
  - `_fill_missing_recipes()` unit tests.
  - Retry logic tests for `_cached_pair()`.
- Strengthened existing test assertions to verify call arguments, not just call counts.

## [1.2.0] - 2026-04-05

### Changed
- Switched to dynamic versioning via `hatch-vcs` — version is now derived from git tags.
- Restored changelog-based GitHub Release notes in publish workflow.
- Added `CLAUDE.md` with release process instructions.

## [1.1.1] - 2026-04-05

### Fixed
- Fixed PyPI publish by updating package version to match git tag.

## [1.1.0] - 2026-04-04

### Added
- **File locking** for concurrent access — multiple CLI processes can safely share the same `discoveries.json` (e.g. web terminal sessions). Uses `fcntl.flock` on Unix, gracefully skipped on Windows.
- **`INFINITE_CRAFT_DATA` env var** — override the default data directory (`~/.infinite-craft-cli/`) for custom deployments.
- **Shared HTTP client** — Infinibrowser requests now use `curl_cffi` with Chrome impersonation (same as the neal.fun API client), replacing the old `urllib` implementation. Better Cloudflare compatibility.
- **`fetch_json()` helper** — cached, sync HTTP fetcher with Chrome impersonation, available for reuse.

### Changed
- `storage.py`: `add()` now re-reads from disk before writing, so concurrent processes don't miss each other's additions.
- Removed `urllib.request` dependency from CLI module.

## [1.0.0] - 2026-04-04

### Added
- Large refactor into modular architecture.
- Own `Element` dataclass, `DiscoveryStorage`, `RateLimiter`, and `InfiniteCraftClient` modules.
- `--version` flag.
- All interactive commands available as non-interactive subcommands: `recipe`, `import`, `export`, `fill`, `unfilled`, `exhaust`, `crawl`, `permute`, `cross`.
- Retry with exponential backoff on API errors.
- Windows compatibility: `readline` guarded, signal handling fallback.
- 162+ unit tests via Bazel, integration test suite against real API.

## [0.1.0] - 2026-04-04

### Added
- Initial release with interactive CLI, element combining, and discovery storage.
