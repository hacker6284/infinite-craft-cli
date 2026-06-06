# Changelog

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
