# Changelog

## [2.0.1] - 2026-08-20

### Changed
- **Pure, loop-free scripts now run immediately instead of queueing.**
  A bare query line (`/steam|mist/`, `fire* , water*`, `x := a* - b*`)
  cannot touch the save, so it now interleaves with a running bulk
  command exactly like `/search` always has — previously it waited in
  the pair queue behind whatever was running. The decision is the
  kernel's (`runs_local`): statically pure AND no until/while/for-each.
  Loops always queue, even pure ones — the queue's Stop/Ctrl-C machinery
  is the only brake on an unbounded pure loop. In the trainer this also
  applies while a confirm prompt is open, matching local commands.

## [2.0.0] - 2026-08-20

### Added
- **The Infinite Craft script language (spec v0.6).** Every non-slash line
  in the REPL — CLI and browser trainer — is now a script; the old
  shorthands (`A + B`, `A ++ B`, `Q * Q`) are one-statement scripts and
  behave as before. The language adds: `;` sequencing, `name := expr`
  bindings (scoped to one script run), `,` union / `-` difference / `&`
  intersection, `/` and `%` known-recipe filters, postfix `(S)*` `(S)**`
  `(S)!` (permute / permutate / exhaust), `A * B` cross, `A ++ B` crawl,
  `[ expr ]` and the session-global `[]` new-elements register, `^(expr)`
  first-discoveries filter, quoted exact element references, `set @ body`
  and `set @x body` for-each, `body -> cond` do-until, `body ~ cond`
  while, and `cond ? a : b` ternary with size arithmetic (`|expr|`,
  comparisons, `&&`, `||`).

  Design guarantees: conditions are statically pure (mutations inside a
  condition are parse errors); every mutating operation's value is the set
  it produced, so pipelines chain; sets are ordered snapshots and pattern
  evaluation is deterministic across hosts; scripts inherit bulk confirms,
  `/auto`, `/target`, rate limiting, and cancellation unchanged. The
  tokenizer generalizes the old convention — whitespace-delimited
  operators are structure, attached characters belong to patterns — so
  multi-word names (`mountain range + ship`) and fnmatch classes
  (`mu[dg]`, leading `[bc]at`) keep working unquoted.

  The parser, static checks, and all pure evaluation live in the kernel
  (`craft.sudo`, lockstep-tested on both backends); hosts drive effects
  through the existing bulk machinery.

- **`/script` runs saved scripts** (recommended extension `.ice`): CLI
  takes a path, the trainer opens a file picker, and
  `infinite-craft script "src"` / `script -f path` run non-interactively.

### Fixed
- **Ctrl-C at an idle REPL prompt now exits cleanly** (`Goodbye!`, exit
  130) instead of dying with a `KeyboardInterrupt` traceback — or, on
  Python 3.11+, hanging while asyncio joined a stdin reader thread still
  blocked in `read()`. Pre-existing since at least 1.9.2; surfaced by
  release stress-testing. Ctrl-C during a running command still cancels
  just that command, and SIGINT during a non-interactive `script` run
  prints `Cancelled.` and exits 130.

### Changed (breaking)
- **`+|` is removed.** `A +| B` is a parse error pointing at `*`; with a
  singleton left side they were the same operation. `/with` is unchanged.
- **Bare words are element references everywhere.** In the old `Q * Q` and
  `A +| Q` forms, bare operands were substring queries; now `fire` means
  the element Fire (error if unknown) and substring matching is explicit:
  `*fire*`. Queries with metacharacters are unchanged.
- **A bare pattern line now prints its matches** (it used to be a usage
  error); `A + B + C` chains combines left-to-right (it used to error).
- **`/permutate` no longer stops at 50 rounds** — it runs until a round
  adds nothing new. Caps are gone everywhere by design; Stop/Ctrl-C and
  script loop conditions are the brakes.
- **The kernel's shorthand machinery is retired, not shadowed.**
  `classify_command_line` recognizes slash commands only, `parse_operands`
  is deleted, the shorthand branches of line validation are gone, and
  `may_bulk_confirm` now answers for scripts by parsing them (a script may
  bulk-confirm iff it contains a mutating statement). Parity fixtures
  exercise the script parser on both generated backends, including exact
  error-message parity.

## [1.11.0] - 2026-08-19

### Added
- **`/auto` — session auto-approve for bulk confirms** (trainer and CLI).
  Bare `/auto` toggles; `/auto on|off|status` are explicit. While on, runs
  over the 200-pair warn threshold print a dim "Auto-approved N pairs"
  line and start immediately instead of asking y/n. Target hits still ask:
  `/target` is an explicit opt-in to pausing, so `/auto` never overrides
  it. The toggle rules and gate live in the kernel
  (`auto_approve_outcome`, `bulk_confirm_required`), shared by both hosts.

- **"Rate limiting in progress" indicator** (trainer and CLI). When the
  request window is exhausted (0/60 slots), the note appears beside the
  rate bar and clears as slots free up. The string comes from the kernel
  (`rate_status_note`) so both hosts read identically.

- **The trainer holds a Web Lock while a run is active**, which exempts
  the tab from Chrome's Memory Saver / Energy Saver freezing under
  current heuristics — so bulk runs keep executing in a background tab.
  The lock is scoped to exactly the lifetime of active work: acquired
  when a run starts, released when the last concurrent run ends, so the
  battery-saver is only defeated while there is real work to protect.
  Best-effort by design — if Chrome tightens the heuristic, background
  runs pause again but nothing breaks. For a guarantee, add neal.fun to
  chrome://settings/performance → "Always keep these sites active" (now
  documented in the README).

## [1.10.2] - 2026-08-18

### Changed
- **HTTP retry and timeout policy moved into the shared kernel.** How many
  attempts, which failures retry, the backoff schedule, and the 30s fetch
  timeout are now kernel decisions (`pair_should_retry`, `ib_should_retry`,
  and friends in `craft.sudo`), consumed by both the CLI and the browser
  trainer — they had been duplicated constants that already drifted once.
  Hosts keep the actual sockets, timers, and abort plumbing.

### Fixed
- **The CLI now retries Infinibrowser rate limits and network failures.**
  The trainer retried 429s with backoff; the CLI gave up on the first
  error, so `/fill` and `/prune` silently failed elements the trainer
  would have recovered. Both hosts now apply the same kernel policy
  (4 attempts, 2s/4s/8s backoff; 404s and 5xx are still returned as real
  answers). The CLI's Infinibrowser timeout goes 15s → 30s to match, and
  the pair API timeout is now explicit instead of inheriting the HTTP
  library's default.

## [1.10.1] - 2026-08-18

### Fixed
- **Bulk runs no longer stall for hours in background tabs.** The rate
  limiter sliced every wait into 50ms timer chunks, so one 60-second
  rate-limit wait was a ~1,200-deep `setTimeout` chain. Chrome throttles
  timer chains five or more deep in hidden tabs to one fire per minute,
  which turned that single wait into a ~20-hour stall — an overnight
  `/exhaust` or `/crawl` in a backgrounded tab simply froze until the tab
  was foregrounded. Waits are now one timer against a deadline, staying
  under the chain-depth threshold, and pressing Stop rejects in-flight
  waits immediately instead of waiting for the next poll tick.

- **A stalled network request no longer hangs a bulk run forever.** API and
  Infinibrowser fetches had no timeout — only the Stop button's abort
  signal — so one request that never completed (laptop sleep/wake, network
  change) froze the run at `n/N` indefinitely. Every attempt now times out
  after 30s and retries with the existing backoff; response-body reads are
  covered by the same guard. Infinibrowser fetches also retry on network
  errors instead of failing the whole element.

- **Dismissing the `/import` file dialog no longer wedges the IB lane.**
  The file-pick promise only settled when a file was chosen; cancelling the
  dialog left the lane worker awaiting it forever, and every later
  `/import`, `/fill`, or `/prune` queued behind it permanently — Stop
  couldn't recover it. Dialog dismissal now settles the promise.

- **Stop during a confirm prompt no longer eats the next keystroke.**
  Stop resolved the confirm without running its cleanup, leaking the
  capture keydown handler, which swallowed the next y/n/Esc pressed into
  an empty input.

## [1.10.0] - 2026-08-18

### Added
- **The browser trainer now syncs with the open page live, in both
  directions.** Previously the trainer wrote discoveries only to the game's
  IndexedDB, which neal.fun reads once at load — so new elements never
  appeared in the sidebar until you refreshed the page. The trainer now also
  pushes each new element into the page's live item list (via the `window.IC`
  hook the game itself exposes), so combines, `/exhaust`, `/crawl`, and
  `/import` results show up in the sidebar the moment they land. Deleting an
  element through the trainer removes it from the page just as immediately.

  The reverse direction is covered too: a lightweight poll adopts elements
  crafted by hand on the page into the trainer's in-memory state — inventory,
  name/id indexes, and recipe index — and follows page-side deletions and
  save resets, so trainer commands see hand-crafted elements without a
  reload. Anything exotic that slips past the poll remains recoverable via
  `/import`. Every page touch is guarded: if neal.fun renames its hook, the
  trainer degrades to exactly the old refresh-to-see behavior.

## [1.9.2] - 2026-08-18

### Fixed
- **The browser trainer's banner announced every release as `(local build)`.**
  The version banner added in 1.9.0 only ever landed on the CLI side; the
  trainer half shipped a hardcoded string, so the bundle served from GitHub
  Pages — the one live extensions and userscripts fetch — always claimed to be
  a local build. It now prints the same stamped version the wheel is named
  with, and the Pages deploy refuses to publish a bundle whose version is not
  the tag being released, matching the gate `publish.yml` already applies to
  the wheel.

### Changed
- **Bulk commands sort their work queue ~6x faster.** Every bulk command
  (`/permute`, `/cross`, `/with`, `/exhaust`, and each crawl generation, in
  both the CLI and the browser trainer) orders its pairs by ingredient-usage
  score before running them. On a full inventory that is ~97,000 pairs, and
  the ordering step dominated the wait before the first API call went out.
  It now takes about 6.6s where it took about 40s. The resulting order is
  byte-identical — this is purely how fast the queue is built.

  The kernel had carried a hand-rolled heapsort since 1.8.4 because the sudo
  standard library's `sort_by` was an insertion sort that froze the trainer at
  that scale. sudocode v0.7.1 and v0.7.3 made the standard sort faster than
  the hand-rolled one, so the bespoke sort is gone and the kernel now
  hand-rolls no sort of any kind.

- **sudocode toolchain pinned to v0.7.3** (from v0.5.0). No generated-code
  behaviour change beyond the above; `defs.bzl` is byte-identical, so no
  build rules moved.

## [1.9.1] - 2026-08-14

### Changed
- **Docs for 1.9.0 surfaces.** README covers `/target`, dual pair/IB queues,
  sticky rate + confirm chrome (`confirm [y/n]>` only), and the version
  banner. The GitHub Pages command list includes `/target`.

## [1.9.0] - 2026-08-14

### Added
- **Sticky chrome, dual queues, and a segmented rate bar.** Hybrid chrome
  shows rate budget, job progress, and the current pair. Pair work and
  Infinibrowser fill/import/prune use independent queues. The rate bar's
  left segment is next-slot wait refill; the right is remaining budget.
  Bulk confirm is instant y/n. Confirm reason sits on the job row next to
  the prompt (`◆ confirm /exhaust … · 331 pairs`); y/n appears only on
  `confirm [y/n]>`.
- **`/target` confirm on craft hit.** Watch for a named result; on hit,
  highlight and ask y (continue) or n/Esc (stop) before more batch/queue
  work, using the same confirm chrome as bulk y/n. Setting a target says
  you'll be asked — it does not auto-halt.
- **CLI version banner.** Dim package version next to the welcome title.
- **Playwright CDP launcher** for live trainer reinjection.

### Changed
- **Shared kernel policy.** Queue accept, lane classification, confirm
  routing, target parse/hit/apply, and rate-bar math live in
  `sudo/craft.sudo`. Python and the trainer call the same rules; hosts
  keep async workers, TTY/DOM paint, clocks, and HTTP.
- **sudocode toolchain v0.5.0.** Codegen is byte-identical to v0.4.0 for
  this project's py/js backends.
- **Cleanup-loop workflow** now uses a residual ledger, greppable signal
  proof, dual-host-only invent classes, and a no-shrink stop. Sudo lockstep
  is the kernel parity guarantee; host `tests/parity` is wiring only.

### Removed
- Low-value tests that re-asserted source, poked private globals as the
  oracle, or duplicated stronger UX / parity / kernel coverage. Behavioral
  harnesses, host-surface smokes, and kernel lockstep stay.

## [1.8.4] - 2026-08-08

### Fixed
- **Large `/permute` / `/permutate` no longer freezes the browser.** Pair
  prioritization used `std.sorting.sort_by` (stable insertion sort, O(n²)).
  A 441-match permute is ~97k pairs; sorting that with insertion sort hung
  Chrome. `prioritize_pairs` now uses an in-kernel heapsort
  (`sort_priority_rows`) with the same total order (score descending, pair
  key, input index) — O(n log n). Comparisons are inlined so sudoc does not
  re-deep-copy row tuples on every sift step. Candidate to upstream into
  `std.sorting` as a large-n alternative to insertion sort.

- **Resolve/add stay in the shared kernel (host shortcut reverted).** v1.8.3
  moved resolve/add onto host name indexes for snappiness; that split logic
  across backends. Hosts again call `resolve_element_boundary` /
  `add_element_boundary` / `add_elements_batch_boundary`. The real cost was
  nested helpers that took the inventory `List` as a parameter: sudoc
  deep-copies every List arg on entry, so `resolve_element` → `get_by_name`
  ×4 re-duped the whole save, and `add_elements_batch` → `find_index_by_name`
  per item did the same on inserts. Membership scans are inlined in
  `resolve_element`, `add_element`, and `add_elements_batch`; pair-list
  membership in `record_recipe` is inlined the same way.

## [1.8.3] - 2026-08-08

### Fixed
- **`/fill` and `/unfilled` no longer stall on large saves.** `unfilled_names_boundary`
  called `is_unfilled(name, recipes)` once per element; sudoc deep-copies every
  Map argument, so a few-thousand-element save paid thousands of full recipe-map
  copies before the first Infinibrowser request. The boundary now builds a
  filled-name set once and scans with O(1) lookups (~2.8s → ~25ms on a 3k-element
  fixture). Both hosts also skip names already filled by a prior lineage in the
  same `/fill` run (local set, not per-name kernel `is_unfilled`).

- **Trainer and CLI feel snappy again on large inventories.** Hot paths were
  rematerializing the entire element list through the sudoc adapter on every
  resolve and every single-element add (`resolve_element_boundary` /
  `add_element_boundary`). Formatting a multi-step `/recipe` did that hundreds
  of times; `/fill` and `/import` did it per lineage step. Hosts now resolve and
  insert-or-ignore against their name indexes and only call string-level kernel
  helpers (`title_case`, `sanitize_element_name`). Full-list boundary calls stay
  for match and bulk pair generation where the whole inventory is required.
  Resolve ×120 on a ~2.5k-element save: ~442ms → ~0.05ms in the trainer.

### Changed
- **`ingredient_usage_counts` and the internal export/prune closure no longer
  sort map keys.** `sorted_text_list` is an O(n²) insertion sort; key order does
  not affect usage counts or the transitive included-name fixed point. Bulk
  prioritize and export/prune drop that pure overhead. Public included-name
  lists remain sorted for stable display.

## [1.8.2] - 2026-08-07

### Fixed
- **`/recipe` no longer hangs on real saves.** Tracing a recipe ran an
  `is_available(name, visited, recipes)` helper that took the whole recipe map
  as a parameter, and sudoc's Python/JS backends deep-copy every map argument
  on entry for value semantics — so the entire recipe index was duplicated on
  every ingredient check of every BFS layer. The per-layer key sort compounded
  it (insertion sort, O(n²)). Readiness is now inlined against a precomputed
  set, keys are never sorted, and the scan stops as soon as the target is
  reached. Traced paths are unchanged; the kernel's own suite dropped from
  285s to ~5s, and the host parity suite from 102s to ~5s.

- **Element names with apostrophes resolve correctly.** Title-casing followed
  Python's `str.title()`, which restarts a word at every non-letter and so
  produced `You Don'T` from `you don't`. That form missed the stored element
  on lookup *and* was sent verbatim to the pair API for undiscovered operands,
  coming back as a spurious Nothing (`Beach + You Don't`). Apostrophes now
  keep the word open after a multi-letter stem (`Don't`, `Baker's`) while a
  single-letter stem still capitalizes (`O'Brien`); both ASCII `'` and the
  curly U+2019 are handled. `resolve_element` also gained a last-resort
  case-insensitive inventory scan so names already stored in the old `Don'T`
  form still resolve.

### Changed
- **sudocode toolchain bumped v0.3.0 → v0.4.0.** `MODULE.bazel` now pins the
  v0.4.0 matched-pair release (ruleset tarball + `sudoc` / `lockstep_diff` /
  `capture_run` / `emit_unpack` binaries). No generated code changed — every
  downstream test stayed cache-identical across the bump, and the 18-test x
  2-backend lockstep suite passes on the new toolchain.

  v0.4.0's breaking change (`record`/`enum` names may no longer contain `_`)
  does not touch `craft.sudo`: `Element`, `RecipeStep`, and `RecipeResult` are
  already underscore-free. The ruleset itself is unchanged apart from
  `versions.bzl`, so `rules_sudo` stays at module version 1.0.0 /
  compatibility level 2 and no rule attributes moved.

## [1.8.1] - 2026-08-07

### Fixed
- **Browser trainer: "Loading game data..." no longer stalls on large
  saves.** Rebuilding the recipe index called the kernel's `record_recipe`
  once per recipe pair, and the generated adapter marshals the entire
  recipe map in and out on every call — quadratic in recipe count, so a
  few thousand recipes took ages at boot (a v1.5.0-era cost that save
  growth made visible, not a 1.7/1.8 regression). Boot and both import
  paths now fold all entries through one `record_recipes_batch` call.

## [1.8.0] - 2026-08-07

### Added
- **Batch API requests now run in priority order — proven combiners first.**
  Every bulk command (`/permute`, `/cross`, `/with`, `/exhaust`, and each
  crawl generation, in both the CLI and the browser trainer) scores each
  pair by how many recorded recipes its two elements already appear in as
  ingredients (self-pairs count twice), and executes highest-scoring pairs
  first. Ties break on the canonical pair key, so ordering is fully
  deterministic — on a fresh recipe index the queue degrades to alphabetical
  pair order. The scoring and sort live in the shared sudo kernel
  (`prioritize_pairs`, ruling 17 in `sudo/DIVERGENCES.md`); the ordering is
  computed once at batch start, and crawls re-score each generation as the
  recipes learned in the previous generation land. Cancelling a batch early
  now leaves the least-promising pairs untried instead of a random tail.

### Changed
- **All kernel sorts now go through `std.sorting.sort_by`** (stable insertion
  sort, explicit top-level comparators) instead of hand-rolled same-module
  insertion sorts — one sort implementation for text lists, the crawl pool's
  name order, and the prioritizer's scored rows. The historical reason for
  hand-rolling (a Rust-backend cross-module `inout` codegen bug in sudoc)
  does not affect the py/js backends this project builds; the caveat stays
  documented in `sudo/DIVERGENCES.md` for future backend adopters.

## [1.7.0] - 2026-08-07

### Changed
- **The remaining duplicated host logic moved into the sudo kernel.** Pair
  generation for `/permute`, `/cross`, `/with`, and crawl generations;
  shorthand operand extraction; the unfilled predicate; command-validation
  error messages; `.ic` import/export folds; Infinibrowser lineage folds;
  element-name storage sanitization; and insert-or-ignore element adds are
  now single kernel implementations shared by the CLI and the browser
  trainer (nine new owner rulings — see `sudo/DIVERGENCES.md`). New parity
  fixtures cover every consolidated area (63 scenarios, up from 34).
- **Crawl semantics are identical in both tools** (CLI semantics won the
  rulings): generation pairs run in sorted-name order, the seed pair is part
  of generation 1 rather than a special case, and any result not already in
  the crawl pool joins the next generation — the trainer previously followed
  insertion order, aborted crawls whose seed pair made Nothing, and only
  followed globally-new discoveries.
- **`/unfilled` and `/fill` agree on what "unfilled" means**: an element
  whose recipes entry is an empty list now counts as unfilled everywhere
  (previously "filled" in the CLI, "unfilled" in the trainer, and orphaned
  by the export closure).
- **Validation errors are structured kernel segments.** Message text lives
  once in the kernel; the CLI styles highlights with ANSI, the trainer
  HTML-escapes every segment and colors highlights.
- **Trainer `.ic` exports use fresh sequential ids with recipes remapped to
  the export closure** (matching the CLI), instead of copying game item ids
  and recipe references verbatim.

### Fixed
- **Browser trainer: shorthand operands were silently truncated.**
  `A + B + C` combined `A` with `B` (dropping `+ C`) because `String.split`'s
  second argument is a result-length limit, not a maxsplit. Same bug in the
  `++`, `+|`, and `*` shorthands. All four now keep the full tail, matching
  the CLI.
- **Browser trainer: validation messages containing `<element>` rendered as
  empty HTML tags** — usage errors like `Usage: <element> + <element>`
  displayed with the placeholders swallowed by the DOM. Segments are now
  escaped before printing.
- **Browser trainer: exports could contain dangling recipe id references**
  pointing at items excluded from the export closure.
- **Browser trainer: imported element names are now sanitized before
  storage** (strip + control-character removal, same kernel rule as the
  CLI), so the two tools can no longer persist different names for the same
  payload.
- **CLI: `/import` no longer crashes on malformed payloads** — Infinibrowser
  lineage steps with missing fields are skipped (and the id-or-text fallback
  matches the trainer) instead of raising `KeyError`, and `.ic` items with a
  missing `text` are skipped along with any recipes referencing them (the
  trainer previously stored the literal name `"undefined"` for those).
- **CLI: `.ic` imports honor the save format's legacy `discovered` flag**
  in addition to `discovery`.

## [1.6.0] - 2026-08-05

### Changed
- **Consolidated onto a single Bazel build path.** The wheel (`//release:wheel.dist`),
  bookmarklet bundles (`//bookmarklet:trainer_js` / `:trainer_min_js`), GitHub
  Pages site (`//bookmarklet:site`), and kernel smoke test
  (`//bookmarklet:kernel_smoke_test`) are now all built by rules_sudo v0.3.0 +
  Bazel; `trainer.js` and `trainer.min.js` are build-only outputs and are no
  longer committed to the repo. Host tests read the built bundle via a
  runfiles locator (`tests/artifact_paths.py`) instead of a fixed path. CI
  (publish/pages/test/release-dry-run) now calls Bazel directly.
- **PyPI publishing is now wheel-only.** `publish.yml` publishes a single
  pure `py3-none-any` wheel built by Bazel `//release:wheel`. The previous
  hatchling-based `python -m build` also produced a source distribution
  (sdist); that sdist is intentionally no longer built or published.

### Removed
- The standalone `sudoc` toolchain path: `scripts/generate.sh`,
  `scripts/sudoc-bin.sh`, `scripts/sudoc-version.txt`, and
  `bookmarklet/minify.sh`. The `.cache/` sudoc-binary download cache and the
  old `sudo.yml` CI workflow are gone too.

## [1.5.0] - 2026-07-22

### Changed
- **Core logic is now generated from one sudo source.** Matching, name
  resolution, recipe recording, lineage tracing, export selection, and
  command validation for BOTH the Python CLI and the browser trainer are
  transpiled from `sudo/craft.sudo` (via [sudocode](https://github.com/hacker6284/sudocode) v0.2.0)
  and verified equivalent by lockstep, parity, and owner-intent test suites.
  The two implementations can no longer drift.
- **The query matcher got dramatically better** (both tools, identically):
  - Real regex alternation: `/cat|dog/` works (previously "too complex").
  - Backslash escapes: `/\d+/`, `/\./`, predefined classes (ASCII).
  - Word boundaries: `/\bfire\b/` matches whole words.
  - Mid-pattern `^`/`$` are true anchors (real-engine semantics).
  - Glob character classes (`a[bc]d`) work everywhere, including the trainer.
  - `!` (exclude) and `^` (first-discovery) filters now combine: `!^steam*`.
  - Empty regex `//` matches everything.
  - The "Regex pattern too complex" gate, scan budgets, and regex timeouts
    are gone — the new engine is linear-time by construction and cannot
    blow up. Recipe lineage tracing is unbounded (no 200-layer cap).
- Trainer behavior aligned with the CLI (deliberate changes): exact-case
  name lookup with title-case fallback, no discovered-flag promotion on
  re-add, exports filter to the recipe transitive closure.

### Removed
- The `regex` PyPI dependency (replaced by the transpiled pure engine).

### Extension (merged from unreleased loader work)

### Fixed
- Chrome extension loader failed on neal.fun because inline `script.textContent` injection is blocked by the site's Content Security Policy. Added a tiny extension-origin `page-bridge.js` that loads via `chrome.runtime.getURL` (CSP-exempt) and executes the fetched trainer with `eval` in the page world so IndexedDB access still works.

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
