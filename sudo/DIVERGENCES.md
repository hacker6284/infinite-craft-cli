# Kernel v2 divergences & rulings (CLI v1.4.2)

This file documents the **kernel v2 re-extraction** in `sudo/craft.sudo`
against infinite-craft-cli **v1.4.2**, base commit `20b51d2` ("Release
v1.4.2"), on branch `sudo-kernel-v2`. That branch carries forward v1's
infrastructure (generate script, parity harness, workflows, vendored stdlib)
on top of the v1.4.2 base and re-extracts the pure kernel from current
sources.

Per the standing rule in sudocode's decision log
(`notes/decision-log.md`, entry *"2026-07-21 — Re-port: pilot was built on a
five-release-stale base"*): *before extracting logic from any external repo,
fetch and verify the local base equals remote HEAD, and record the base
commit in the port's DIVERGENCES/report*. This header is that record for the
v2 re-port.

## Why v2, not a v1 patch

The prior port (the superseded v1 `DIVERGENCES.md`) targeted a base that was
**27 commits / ~15k insertions** stale. In that gap the matching subsystem
grew (scan budgets, query-length caps, regex classification, parse filters),
the trainer churned heavily, storage semantics shifted, and a hand-maintained
parity suite landed as `tests/test_trainer_parity.py`. That test file is now
the authoritative **spec input** for this kernel's command-parsing surface
(`_classify_command_line`, `_validate_command_line`, `_parse_query_filter`,
`_match_elements`, etc.).

The single biggest structural change from v1: **the kernel is one canonical
implementation** — the Python CLI's behavior — not two parallel `_js` / wired
siblings. v1's six Python/JS divergences are now **rulings baked into the only
behavior**. There are no `_js`-suffixed functions in `sudo/craft.sudo` (only a
header comment noting the deleted v1 pattern). Hosts that still disagree
(today: the browser trainer) will change at integration time, not via dual
kernel paths.

---

## Rulings (7)

Each row is kernel-canonical. Rationale is one line; facts are verified
against `sudo/craft.sudo` and upstream `src/infinite_craft_cli/`.

| # | Ruling | Rationale |
|---|--------|-----------|
| 1 | **Unbounded trace BFS** — no depth cap | Matches CLI `do_recipe` / unbounded `while not found`; the 200-layer cap was only a JS browser-safety artifact, never a CLI invariant. |
| 2 | **No discovered-flag promotion on re-add** | Matches `storage.add` insert-or-ignore; persisted storage is the source of truth. |
| 3 | **Case-sensitive `get_by_name`; `resolve_element` falls back raw → stripped → title-cased → ASCII case-insensitive**. `title_case` is `str.title()` **except** multi-letter apostrophe stems stay in-word. Apostrophe *code points* are never folded | `get_by_name` still matches `storage.get_by_name`'s exact lookup. `str.title()`'s `"You Don'T"` is rejected (§3) because it mangled contractions for the pair API. U+0027 vs U+2019 stay distinct element names (the live API can have both); resolve does not alias them. |
| 4 | **Export/prune filter = transitive recipe closure** (`included_element_names(recipes)` only) | Matches v1.4.2 `_included_element_names` (cli.py ~2518–2547); pure orphans are export-excluded and prune targets. |
| 5 | **No fnmatch/glob safety gate** | Vendored `regex.sudo` is Thompson-NFA / Pike-VM, linear in input length — wildcard-count / consecutive-star heuristics are unnecessary. |
| 6 | **No 512-char name truncation** | Only the **query** is capped at 512; names match in full. JS `name.slice(0, 512)` existed only to bound in-browser `RegExp` cost. |
| 7 | **No scan budget / regex timeout**; only error is `"Invalid regex pattern"`; full NFA regex — real grouping `()`, quantifiers over groups, nested alternation, `^`/`$`/`\b`/`\B` assertions | Same linear-time engine; CLI `MATCH_SCAN_BUDGET` / `REGEX_TIMEOUT` and JS `REGEX_TIMEOUT_MS` guard PCRE/backtracking the NFA cannot exhibit. `regex.sudo` v0.3.0 compiles `(...)` as real (always non-capturing) grouping — `(ab)+`, `(a+)+`, nested `(x(y|z))w` — and `^`/`$` as true zero-width position assertions anywhere in the pattern, not just at branch edges. `(?:...)` is **not** separately recognized (a leading `?` inside a group with nothing preceding it to quantify is a parse error) — since every group is already non-capturing, this is a confirmed syntax gap, not a matching-correctness bug. Upstream's blanket "any `\|` is too complex" rejection, and its lack of any grouping support at all, have no counterpart here. |

### 1. Unbounded trace BFS

`compute_layers` follows Python's shape: `while not found`, break only when a
layer produces no new names — no iteration ceiling. Test `"trace_recipe
unbounded depth over 200 layers"` builds `C0`…`C300` and asserts
`trace_recipe(..., "C300")` returns `Steps` with length **301**. v1's JS-only
200-layer cap would have failed that chain.

**Why `/recipe` used to hang (canonical rationale; the code comment points
here).** Three costs were removed, none of which change what `trace_recipe`
returns:

1. **Readiness is inlined** against a precomputed `has_pairs` set rather than
   a helper that took the whole recipe `Map` as a parameter. sudoc's JS/Python
   backends deep-copy every Map/Set arg on entry for value semantics, so the
   old `is_available(n, visited, recipes)` deep-copied the entire recipe index
   on *every pair check of every layer* — chain-300 alone took tens of seconds
   in both hosts. `visited.has(n) or is_base_element(n) or not has_pairs.has(n)`
   is exactly equivalent: `not has_pairs.has(n)` covers both "absent from
   `recipes`" and "present with an empty pair list".
2. **Keys are never sorted.** `sorted_text_list` runs the stdlib
   `sorting.sort_by`, which was an insertion sort — O(n²) per layer — when
   this was written. Sorting cannot
   affect the result: a layer's membership is computed against the *previous*
   layer's `visited`, which is not mutated mid-layer, so the committed set is
   independent of scan order, and pair choice is per-result list order.
3. **The scan stops the moment `target` is reached** instead of finishing the
   layer. Safe because `target`'s ingredients were available at layer start —
   i.e. committed in a *strictly earlier* layer — and `backtrack_steps` only
   walks parent links from `target`, never touching co-layer siblings. The
   returned `parent` map may therefore omit siblings the old code included;
   nothing reads it except that backtrack.

A reverse-index frontier (`dependents[ing] += result`) was tried and rejected:
building it is itself O(n²) under value-semantic map updates when many recipes
share the bases.

Test `"compute_layers readiness and ordering invariants"` pins all four
behaviours these rest on — orphan and empty-recipe ingredients counting as
available, first-*ready* pair winning in list order, and an unsorted key list
with a mid-layer stop still yielding the correct ancestor chain.

**Related kernel snappiness (same sudoc Map/List cost model):**

- `unfilled_names_boundary` precomputes a filled set once (never call
  `is_unfilled` per element — each call deep-copies the recipe map).
- `ingredient_usage_counts` and the internal `included_element_names` closure
  no longer sort keys/items; insertion-sort was pure overhead for commutative
  aggregates / set fixed-points. The public included-names boundary still
  returns a sorted list for stable display.
- **Do not reimplement resolve/add on the host for speed.** Shared kernel
  ownership is the point. Nested helpers that take the inventory `List` as a
  parameter re-deep-copy it on every call — `resolve_element` therefore
  inlines membership scans (not `get_by_name` ×4), and `add_elements_batch`
  inlines insert-or-ignore (not `find_index_by_name` / `add_element` per item).
- **`prioritize_pairs` used a hand-inlined heapsort** (`sort_priority_rows`)
  because `std.sorting.sort_by` was insertion sort and ~10⁵ pairs from a large
  `/permutate` hung the browser. **Retired at sudoc v0.7.1.** v0.6.0 made
  `sort_by` a merge sort but its generated code still deep-copied the whole
  list four times per merge pass, so the builtin still lost; v0.7.1 fixed the
  swap and added copy-on-write lists, and v0.7.3 made COW uniform across every
  composite and stopped read-only destructures copying their source. Measured
  on 441 choose-2 = 97,020 pairs, py backend, byte-identical output ordering at
  every step: heapsort 40.2s, `sorting.sort_by` 70.1s on v0.7.0, 26.1s on
  v0.7.1, **6.6s on v0.7.3**. The kernel now hand-rolls no sort of any kind.

### 2. No discovered-flag promotion on re-add

`add_element` (~188–193): if the name already exists, return `false` without
mutating emoji or `first`. Re-add with `first=true` cannot promote a stored
`first=false` (covered in `"title_case resolve get_by_name add record"`).

### 3. Case-sensitive lookup + title-case fallback

`get_by_name` is exact `e.name == name`. `resolve_element` tries the raw name,
then stripped, then `title_case(stripped)`, then an ASCII **case-insensitive**
scan of the same code points, else synthesizes `Element(name=title, ...)`.
Apostrophe **code points are never folded**: `"You Don't"` (U+0027) and
`"You Don't"` (U+2019) remain distinct names in inventory, recipes, and pair
API calls — the game can have both, so resolve must not alias them.

`title_case` approximates Python `str.title()` with a stem-length-sensitive
apostrophe rule (letter case only; the apostrophe character is preserved):
a **multi-letter** stem stays in-word (`don't` → `Don't`, not `"Don'T"`);
a **single-letter** stem still starts a new word (`o'brien` → `O'Brien`).
Both ASCII `'` and U+2019 count as apostrophes for that rule.

The Don'T form was a real trainer bug (`Beach + You Don't` sent as
`You Don'T` → Nothing). Cross-matching curly IB imports to keyboard ASCII
was deliberately *not* taken — that would merge two possible API identities.

See residual JS §5 for the older `\b\w` replace disagreements.

### 4. Export/prune transitive closure (re-derived for v1.4.2)

`included_element_names` (~347–373) takes **only** `recipes` (not the elements
list). Both `export_elements` and `orphan_candidates` call it. This is the
current v1.4.2 `_included_element_names` (cli.py:2518–2547), itself a refactor
from the stale v1-port base: **not** "the same closure as v1 DIVERGENCES" —
input signature and shared-with-prune wiring changed upstream; this port
re-derived from current source. Bases + non-empty recipe results seed the set;
ingredients close transitively. Pure orphans are excluded from export and are
exactly prune's candidate set (test `"included_element_names orphan export
v1.4.2"`).

### 5. No fnmatch/glob safety gate

Glob path is straight `regex.glob_match(pattern, name, true)` with no
wildcard-count or consecutive-star checks (`element_matches_pattern` ~431–432).
Test `"ex-gate pathological patterns now work"` asserts a 12-wildcard glob
(`*a*b*c*d*e*f*g*h*`) matches — the old CLI `wildcards > 10` fnmatch-safety
gate would have rejected it.

### 6. No 512-character name truncation

Matching always uses the full element name. Only the query string is capped:
`query.length > 512` → `"Query too long (max 512 characters)"` (exact upstream
message) in `match_elements` / `validate_query_at_enqueue`.

### 7. No scan-time budget / regex timeout; full NFA regex engine (v0.3.0)

`_regex_is_safe` / `MAX_REGEX_BODY_LENGTH` / `|`-rejection / nested-quantifier
rejection are **not** ported. The string `"Regex pattern too complex"` does not
appear in `craft.sudo`. The only regex error is `"Invalid regex pattern"`.

Hosts (CLI, browser trainer) **may** still wrap kernel calls with an outer
wall-clock guard as defense-in-depth; the kernel itself is deterministic and
linear-time, so none is built in.

**Behavior change (testable) — kernel INTENTIONALLY EXCEEDS upstream here:**
upstream `_regex_is_safe` rejected *any* pattern containing `|` outright as
"too complex", even though Python's `regex` module supports real
alternation — the rejection was a backtracking-blowup safety gate, not a
feature gap. `regex.sudo`'s Thompson-NFA/Pike-VM engine is linear-time by
construction and cannot exhibit catastrophic backtracking, so that fear is
obsolete: `regex.sudo` implements real alternation (`|` splits the pattern —
or a group body — into branches via the engine's native NFA `Split`
fan-out). `/cat|dog/` is a valid kernel regex meaning "cat" OR "dog", not
the seven-character literal run `"cat|dog"`. This is a deliberate widening
beyond upstream, not a compatibility requirement — upstream v1.4.2's frozen
hand-written reference implementations (the parity oracle this repo diffs
against) still hard-reject any `|` as "too complex". This repo's own
kernel-backed CLI and JS trainer are NOT among those rejectors: they
inherit `regex.sudo`'s real alternation directly, with no separate porting
needed (see `tests/test_bulk.py::TestDoCross::test_complex_regex_cross_combines_matching_elements`,
which passes `/(a|aa)+/` end-to-end through `do_cross`). Test coverage: `"element_matches_pattern real alternation cat
or dog"` — `/cat|dog/` matches `"Watchdog"` (contains "dog"), does not match
`"Elephant"` (contains neither). `"ex-gate pathological patterns now work"`
also covers `/a|b/` now matching bare `"a"` and `"b"` (previously
literal-pipe-only, matched neither).

**Second gap closed — backslash escapes:** upstream (both the CLI and the
JS trainer) accept `/\d+/`, `/\./`, and similar backslash-escaped patterns
through their safety gates (they only special-cased `|` and length/wildcard
counts, never backslash). `regex.sudo` previously treated `\` as an
ordinary literal character, so `/\d+/` meant "a literal backslash followed
by one or more 'd' characters" (the `+` was always a real quantifier, just
applied to a literal `d` instead of a digit class), not "one or more
digits" — a real behavioral gap against upstream. `regex.sudo` implements
backslash escapes: metacharacter escapes (`\.` `\*` `\+` `\?` `\[` `\]`
`\^` `\$` `\|` `\\` `\/` `\{` `\}` `\(` `\)`) and predefined classes `\d`
`\D` `\w` `\W` `\s` `\S`. **Caveat, also documented in `regex.sudo`'s own
header:** the predefined classes are ASCII-only subsets (this engine has
no Unicode tables, same family as the existing ASCII-only case folding) —
python's `\d`/`\w` are Unicode-aware and match e.g. the Arabic-indic digit
`٣` or the letter `É`; this engine's `\d`/`\w` do not. `\b`/`\B` word
boundaries (ASCII `[A-Za-z0-9_]` word class, same divergence as `\w`) **are
now implemented** as real zero-width assertions — as of v0.3.0 this is no
longer a parse error (v0.2.0's `regex_is_valid("\\b") == false` is now
`true`). Test coverage: `"element_matches_pattern backslash escape
digits"` — `/\d+/` matches `"Area 51"` (contains digits), does not match
`"AreaX"` (contains none).

**Third gap closed (v0.3.0) — real grouping, quantified groups, nested
alternation, and full-position anchors:** `regex.sudo` previously had no
grouping construct at all — `(` and `)` were ordinary literal characters,
so a pattern like `/(a+)+/` meant the 5-character literal run `"(a+)+"`
(one `(`, one-or-more `a`, one `)`, then a dangling `+` with nothing to
repeat — actually a parse error in that older scheme) and top-level `|`
split the *whole raw pattern* on literal `(`/`)` text, not a semantic
group. v0.3.0 compiles `(...)` as a real (always non-capturing — there
are no capture slots at all, so there is no separate "capturing vs.
non-capturing" distinction to make) grouping metacharacter: quantifiers
apply to the whole preceding group (`(ab)+`, `(a+)+`, `(ab){2}`), `|`
inside a group body is scoped to that group and nests arbitrarily
(`(a(b|c)d)+`, `(x(y|z))w`), and `^`/`$` are true zero-width position
assertions that can appear anywhere in a branch — not just bound to a
branch's start/end — so a mid-pattern `^` (e.g. `"a^b"`) can never be
satisfied (the position right after `a` is never position 0), matching
python's `regex`/`re` semantics exactly (verified against `python3
regex.search(...)` as the oracle for every anchor-placement case in
`regex.sudo`'s own test suite). Host coverage: CLI test
`test_nested_quantifier_group_matches_names_with_a` — `/(a+)+/` now matches
any fixture element name containing an `a` (e.g. `Water`, `Earth`), not the
empty set; `test_alternation_quantifier_group_matches_names_with_a` —
`/(a|aa)+/` matches the same set via a quantified alternation group.
The underlying oracle is `regex.sudo`'s own test suite in the sudocode
stdlib (`"regex non-capturing groups"`, `"regex mid-pattern anchors"`).

**Confirmed gap, not papered over — no `(?:...)` non-capturing-group
syntax:** because every `(...)` group is already non-capturing by
construction (the engine has no capture-slot concept to opt out of), there
is no dedicated `(?:...)` spelling the way PCRE/python `re` have one.
Writing it does not silently degrade to plain grouping — inside a group
body, a leading `?` with no preceding atom to quantify is a parse error
(`"invalid pattern: quantifier with nothing to repeat"`), so the whole
pattern fails to compile and the CLI reports `"Invalid regex pattern"`.
Host coverage: `test_non_capturing_group_syntax_is_unsupported` —
`/(a|(?:aa))+b/` returns `matches == []` and `err == "Invalid regex
pattern"`, not a match. Query authors should write bare `(aa)` instead of
`(?:aa)`.

---

## Residual JS-side behavior changes (post-kernel integration)

Not implemented this turn — documentation of what will change in the browser
once `bookmarklet/trainer.js` is wired to this kernel (later task). Descriptions
reflect **current** trainer.js on the v1.4.2 tree.

### 1. Glob character-class support

**Current:** `fnmatchToRegex` (~479–513) already implements `[...]` classes
(including `!`/`^` negation) and `elementMatchesPattern` enters glob mode on
`/[*?[\]]/` — so bracket globs are **not** the v1-era "escape `[`/`]` and fall
through to substring" story anymore; upstream closed much of that gap before
this re-port. **After wiring:** character classes continue to work via
`regex.glob_match`, aligned with the CLI/kernel (any remaining edge-case drift
between hand-rolled `fnmatchToRegex` and the NFA glob engine goes away).

### 2. `^` first-discovery filter scope

**Current:** `parseQueryFilter` (~422–434) strips a leading `^` into `onlyNew`;
`matchElements` (~541–576) applies that filter for every caller.
`doSearch` (~877–882) no longer peels `^` locally — it just calls
`matchElements`. So the v1 residual ("only `/search` honors `^`") is **already
closed** in today's trainer. **After wiring:** behavior stays shared-matcher
semantics via kernel `parse_query_filter` / `match_elements` (no regression;
single implementation).

### 3. `/recipe` BFS depth cap

**Current:** `doRecipe` (~891–949) hard-caps BFS with
`for (let depth = 0; depth < 200 && !found; depth++)`. Past 200 layers it
reports that the full lineage cannot be traced. **After wiring:** arbitrarily
long lineages succeed, matching the CLI and kernel `compute_layers` (no depth
argument).

### 4. Discovery-flag promotion on re-add

**Current:** `addElement` (~260–277) promotes `discovered` false→true when
re-encountering an existing item with `discovered=true`. **After wiring:**
re-add is insert-or-ignore only — no retroactive promotion — matching
`add_element` / `storage.add`.

### 5. Case-sensitive lookup + title-casing algorithm

**Current:** `getByName` (~247–249) lowercases both sides; `resolveElement`
(~251–258) title-cases via `\b\w` replace. **After wiring:** exact
case-sensitive lookup first, then ASCII-approximated Python-`.title()` fallback
(`title_case` / `resolve_element`), plus a case-insensitive last resort.
Known input disagreements (`title_case` now diverges from `str.title()` on
apostrophes as well — ruling 3 / §3):

| input | kernel `title_case` (current) | Python `str.title()` | legacy JS `\b\w` |
|-------|-------------------------------|----------------------|-------------------|
| `3d printer` | `3D Printer` | `3D Printer` | `3d Printer` |
| `HELLO world` | `Hello World` | `Hello World` | `HELLO World` |
| `under_score name` | `Under_Score Name` | `Under_Score Name` | `Under_score Name` |
| `co2 gas` | `Co2 Gas` | `Co2 Gas` | `Co2 Gas` |
| `you don't` | `You Don't` (apostrophe kept in-word) | `You Don'T` | `You Don't` |

### 6. Export transitive closure

**Current:** `doExport` (~1408–1428) maps every `_items` entry into the payload
with no filtering. **After wiring:** exports drop pure orphans via
`included_element_names` / `export_elements`, with the same excluded accounting
as the CLI.

### Ex-gate / truncation deletions (rulings 5–7)

These are separate from the original six v1 divergences; they come from
matching-subsystem rulings new to this extraction.

**Current:** trainer.js (~14–22, ~445–539) still has `fnmatchIsSafe`,
`regexIsSafe`, `REGEX_TIMEOUT_MS`, `MAX_REGEX_BODY_LENGTH`, `MATCH_SCAN_BUDGET`,
and `name.slice(0, 512)` / `nameLower.slice(0, 512)` before match. Patterns with
>10 wildcards, nested quantifiers, bodies >200 chars, or any `|` fail as
"Regex pattern too complex"; names longer than 512 chars are truncated before
matching. **After wiring:** those gates disappear. Patterns the browser
currently rejects as "too complex" simply run; matching considers the **full**
element name. This is a strict **widening**: nothing that matches today stops
matching; some queries that error today start succeeding.

---

## sudo language friction

Where sudo (v0.1) made this port harder than Python/JS. Items carried from v1
that remain true of the v2 kernel, then findings new to this extraction.

### Still true (carried from v1)

- **No cross-directory stdlib import path.** `import strings` / `import regex`
  resolve only to `.sudo` files beside the importer. This tree vendors
  `strings.sudo`, `sorting.sudo`, and `regex.sudo` next to `craft.sudo` rather
  than importing from `sudocode/stdlib/`; vendored copies can silently drift
  from upstream stdlib.

- **No positional tuple field access (`t.0` / `t.1`).** Only named-field record
  access works. Pulling a field from a tuple needs `a, b = t` destructuring (not
  inline in a `for` header — `for a, b in ...` is Map key/value iteration only).
  Multi-field values read more than once are more ergonomic as `record`s.

- **`List<T>.sort()` only for `int` / `float`.** Sorting anything else goes
  through `std.sorting.sort_by` (a stable merge sort since sudoc v0.6.0) with
  a top-level comparator. **Updated v1.8.0:** `craft.sudo` now does exactly that for all
  three of its sorts (`sorted_text_list`, the crawl pool's name order, and
  the prioritizer's decorated rows) — the hand-rolled same-module insertion
  sorts are gone. This is viable because this repo builds only the py/js
  backends; the Rust cross-module `inout` bug below still stands as a
  caveat for prospective Rust/Zig backend adopters. **Updated 2026-08-07:**
  fixed in sudoc v0.3.0 (present in the v0.4.0 toolchain pinned here); see
  the inout entry below. With no closures, the prioritizer's comparator
  reads everything it needs (score, pair key, input index) from the
  decorated tuples themselves.

- **Function values are top-level refs only** (no closures, no safe
  module-qualified pass-through as a function value). A local wrapper would
  still be required to pass `strings.lex_less` into a higher-order `sort_by` if
  that path were used.

- **No built-in int-to-text.** The deep-lineage test synthesizes `C0`…`C300` via
  a hand-rolled base-10 `int_to_text` helper.

- **No Unicode casing tables.** `title_case` / `strip_spaces` (and
  `strings.to_upper` / `to_lower`) are ASCII-only approximations of Python
  `str.title()` / `str.strip()`. Real player-submitted names may include
  non-ASCII; this is a fidelity gap, not a style choice.

- **Explicit parens for `not (...)` and mixed `and`/`or`.** e.g.
  `if not (title == name)` in `resolve_element` — the language does not guess
  where Python and C disagree.

- **Swift empty-match-arm `skip` bug.** `skip` alone in a match arm emits an
  empty Swift `case`, which the compiler rejects. Spec documents `skip` as the
  empty-arm idiom; workaround is a dummy statement (`assert true` / similar).

- **Zig unused per-field match binder.** Zig codegen emits a `const` for every
  variant payload binder; unused binders are hard errors. sudo requires full
  binder arity (no per-field `_` in multi-arg patterns). Workarounds fold the
  binder into an always-false assert (`assert n.length < 0`).

- **Rust cross-module `inout` codegen bug.** Looking up callee param signatures
  only in the current module means cross-module `inout` (e.g.
  `sorting.sort_by`) drops `&mut` and fails to typecheck. **Updated v1.8.0:**
  `craft.sudo` now imports `std.sorting` and uses `sort_by` throughout — safe
  because this repo's lockstep builds py/js only, where the bug does not
  reproduce. Any future Rust/Zig backend adoption must fix this codegen bug
  first (or reintroduce same-module sorts).
  **Updated 2026-08-07:** fixed upstream — sudoc's Rust backend now resolves
  callee signatures program-wide (regression:
  `conformance/multimodule/xmod_inout`); the fix is in every sudoc release
  ≥ v0.1.0, including the v0.4.0 toolchain this repo pins. No longer a
  blocker for Rust backend adoption.

- **Record/enum-through-export-signature loses boundary intent.** `text` fields
  nested under a named `record`/`enum` in an `export func` signature degrade to
  raw code-point arrays at the JS host boundary. Workaround: keep
  `record Element` / `enum RecipeResult` internal; expose `_boundary` adapters
  with tuple/primitive signatures (`(text, text, bool)`, etc.) — see
  `resolve_element_boundary`, `add_element_boundary`, `trace_recipe_boundary`,
  and peers (~812–858).

**Counterpoint (boundary-safe shape that just works):** `Map<text,
List<(text, text)>>` used directly on `export func` signatures
(`record_recipe`, `record_recipes_batch`, `included_element_names_boundary`,
etc.) needed **no** wrapper and lockstep-passed across backends. The friction
is specifically record/enum nesting, not all composite types.

### New in this v2 extraction

- **`\0` is not a valid string-literal escape.** Supported escapes are
  `\" \\ \n \t \r \u{...}` only; `sudoc check` reports
  `unknown escape \0 in " literal`. `exhaust_pairs` (~207–222) encodes a
  `(text, text)` pair as a single `Set<text>` membership key (no
  `Set<(text,text)>` in v1) via `ka + "\u{0}" + kb` — the Unicode-escape form
  of NUL, which is supported.

- **`elif` is not a keyword.** Only two-token `else if` parses. Python's
  `elif` produces a confusing error —
  `expected a statement (an expression alone must be a call)` — on the **line
  after** the bad token, not on `elif` itself. The parser treats something
  upstream as a dangling expression; the real defect (keyword typo) is easy to
  miss.

- **`_` is not a valid binder (general rule).** Not only "no per-field
  wildcard in multi-arg variant patterns" (Zig note above): `_` cannot be
  **any** binder name — destructuring like
  `q, _, _ = some_3_tuple()` yields `expected binder, found Underscore`, and
  so does `case Err(_)`. Every discard in `craft.sudo` uses a real name:
  - `element_matches_pattern` (~426–428): `case Err(msg)` then
    `assert msg.length >= 0` (also satisfies Zig unused-binder pressure);
  - `validate_query_at_enqueue` (~654–655):
    `q, qf_exclude, qf_only_new = parse_query_filter(query)` with a tautology
    assert to "use" the flags;
  - `validate_command_line` (~724–726):
    `with_elem, query = parsed.unwrap()` then `assert with_elem == with_elem`.

- **Zig backend bug (FIXED upstream): cross-module generic type identity.**
  Confirmed compiler bug, not an implementer mistake. Symptom when building
  for Zig:

  ```
  craft.zig:1940:37: error: expected type 'craft.Res_bool_List_i64', found 'regex.Res_bool_List_i64'
          var r: Res_bool_List_i64 = (try regex.regex_search(...));
  regex.zig:82:31: note: union declared here
  pub const Res_bool_List_i64 = union(enum) { Ok: bool, Err: List_i64 };
  craft.zig:72:31: note: union declared here
  pub const Res_bool_List_i64 = union(enum) { Ok: bool, Err: List_i64 };
  ```

  Root cause: `regex.regex_search` returns `Result<bool, text>`, monomorphized
  to a named `Res_bool_List_i64` **independently per module**. Zig treats the
  two byte-identical unions as distinct nominal types. Assigning the call
  result to a local annotated with craft's copy of the type is a type error.

  **Not fixed** by rebuilding sudoc from HEAD at commit `4af727d` ("zig: scope
  short-circuit temps via nested else blocks; guard Result/Option binders") —
  that commit addresses a different Result/Option codegen issue, not
  cross-module type identity.

  **Workaround in `craft.sudo`:** do not bind the cross-module result to a
  typed intermediate; use it as the `match` scrutinee inline
  (`match regex.regex_search(body, name, true)` with `case Err(msg)` /
  `case Ok(matched)` — see `element_matches_pattern` ~425–430). That path
  avoids a pre-declared named local and happens to dodge the bug. It is
  **fragile and syntax-shape-dependent**, not a real fix: assign-then-match
  still breaks; match-on-inline-call happens to take a different codegen path.

  **Recommendation:** `backend_zig` should give each cross-module generic
  instantiation a single canonical identity (always reference the defining
  module's emitted type at use sites, or hoist shared instantiations into one
  file all modules import). Worth a real bug report to the Zig backend lane
  rather than treating the workaround as long-term sufficient.

  **Updated 2026-08-07:** fixed in sudoc — the Zig backend now emits a shared
  `sudo_types.zig` giving cross-module monomorphized generics one canonical
  identity (regression: `conformance/multimodule/xmod_generics`); the fix is
  present in the v0.4.0 toolchain this repo pins.

---

## Consolidation rulings (2026-08-07, kernel-consolidation release)

Owner rulings from the host-code audit that moved the remaining duplicated
pure logic into the kernel. Each row names the canonical behavior and which
host changed. Parity coverage: every area below now has fixtures in
`tests/parity/fixtures.json` (ops `operands`, `permute_pairs`, `cross_pairs`,
`with_pairs`, `unfilled`, `validate_segments`, `crawl_pairs`, `sanitize`,
`ic_batches`, `lineage_batches`, `export_items`, and — as of the v1.8.0
prioritizer release — `prioritize_pairs`).

| # | Ruling | Canonical | Changed host |
|---|--------|-----------|--------------|
| 8 | **Operand extraction** (`parse_operands`) — split on the separator's FIRST occurrence; the tail keeps later separators (`"A + B + C"` → `("A", "B + C")`) | Python `str.split(sep, 1)` maxsplit semantics | JS — `String.split`'s length-limit second argument silently truncated the tail (`A + B + C` combined `A` with `B`) |
| 9 | **Pair generation kernel-owned** — `permute_pairs`, `cross_pairs`, `with_pairs` join `exhaust_pairs` in the kernel; hosts only map boundary tuples back to host objects | (behavior already agreed) | none — 4 duplicated loop copies deleted |
| 10 | **Unfilled predicate** (`is_unfilled`) — an element whose recipes entry exists but is an EMPTY list is unfilled; base elements are never unfilled; both hosts use kernel `is_base_element`, hard-coded base sets deleted | JS semantics (consistent with `included_element_names`'s empty-list treatment) | Python — key presence alone previously counted as filled |
| 11 | **Validation errors as segments** — `validate_command_line_segments` returns `(text, highlight)` lists; Python styles highlights with ANSI, the trainer HTML-escapes every segment and wraps highlights in a span; `validate_command_line` = segment concat. Both hosts' DISPATCH paths are validation-first too: every usage/pipe/operator error a classifiable line can produce is rendered from kernel segments, never hand-built. Residue: `/search` and `/recipe` usage strings stay host-side by construction — they are local commands outside `classify_command_line`, so the kernel validator does not model them (trainer `/import` also dispatches before validation: bare `/import` opens the file picker) | kernel segments | both — Python's ~95-line re-implementation (color injection) deleted; trainer previously printed kernel messages as raw HTML, so `  Usage: <element> + <element>` swallowed `<element>` as a tag |
| 12 | **Crawl generations** (`crawl_generation_pairs`) — pool iterated in sorted-name order; self-pairs included; the seed pair is part of generation 1 (no special-cased initial combine); a generation's results join the pool when not already in it, regardless of prior global discovery; tried-keys are kernel-encoded and returned to the host | Python semantics on all three counts | JS — iterated insertion order, special-cased the seed combine (early-return on Nothing), and admitted only globally-new discoveries |
| 13 | **Import folds** — `ic_save_to_batches` (accept both `discovery` and `discovered` flags; sanitize names; skip items whose text is missing or sanitizes to empty, dropping refs to their ids; drop refs to unknown ids) and `lineage_steps_to_batches` (tolerant id-or-text extraction; skip steps with any missing name; dedup elements, first occurrence wins) | merged: JS's tolerant field handling + Python's dedup/sanitization; missing-text items skip rather than crash (Python) or store garbage names (JS) | both — Python previously KeyError'd on malformed lineage payloads and on `.ic` items without `text`, and read only `discovery`; JS previously skipped sanitization and dedup and stored `"undefined"` for missing text |
| 14 | **Export builder** (`build_export_items_boundary`) — fresh sequential ids 0..n over the export closure in storage order; recipe pairs remapped; pairs referencing excluded elements dropped | Python semantics (structurally no dangling ids; original game ids carry no round-trip value once internally consistent) | JS — kept game item ids and copied `item.recipes` verbatim, which could emit dangling recipe ids; trainer exports now also draw recipes from its recipe index rather than raw save data |
| 15 | **Name sanitization** (`sanitize_element_name`) — storage normalization = strip ASCII whitespace + drop C0 controls, DEL, C1 controls, U+2028/U+2029; applied by both hosts before every storage write; display sanitization (TTY escapes / DOM escaping) stays host-side | kernel rule (ASCII-table approximation of Python's `isprintable()` filter) | both — Python's rule was `str.strip()` + `isprintable()` (exotic unprintables like unassigned code points are now stored rather than dropped; terminal display still filters them at print time); JS previously stored names completely unsanitized, so the two hosts could persist different names for the same payload |
| 16 | **Orphaned exports adopted** — `storage.add`/`add_batch` (CLI) and `addElement`/`addElementsBatch` (trainer) route the insert-or-ignore decision through `add_element_boundary`/`add_elements_batch_boundary`; `orphan_candidates_boundary` now backs the CLI's `/prune` too. `get_by_name_boundary` stays unadopted by design: both hosts keep an O(1) exact-case index with the identical contract (guarded via the `resolve` parity op); routing every display-path lookup through an O(n) boundary would be pure overhead | kernel decides, host persists | both (mechanical) |

| 17 | **Batch execution order is prioritized** (`prioritize_pairs`, v1.8.0) — every batch of API pairs executes proven combiners first: pair score = ingredient-usage count of a + count of b (`ingredient_usage_counts`: +1 per ingredient slot per recorded recipe, self-pairs count twice, results never count), descending; ties break on ascending canonical pair key. Sorted once at batch start (no live re-scoring). Wired at each host's single batch choke point (`_combine_pairs` / `runPairsInner` + the crawl generation loop), so permute, cross, with, exhaust, and every crawl generation share it. Since the cache-first change, `prioritize_pairs_boundary` also takes the host's cached pair keys (NUL-joined canonical form, same convention as `lucky_pairs`' tried list) and promotes cache hits to the front via `cache_first_pairs` — hits cost no rate-limit slot, so a bulk run surfaces every already-known result before spending its first slot on a miss; order stays stable within the hits and within the misses | kernel sort, spelled out explicitly — no host sort-stability reliance | both — all batches previously ran in generation order |

Supersession note: ruling 12's sorted-name generation order is the kernel's
*generation* order (and the prioritizer's tie-break). As of ruling 17,
*execution* order within a generation is priority order — recipe-usage score
descending, sorted pair key on ties — so determinism survives: on an empty
recipe index the prioritizer degrades exactly to pair-key order.

| 18 | **Hive-mind relay policy is kernel-owned** (v2.3.0) — the shared-cache relay coordinates effects, but every *policy* value both hosts must agree on lives in the kernel and is called identically by CLI and trainer: `effective_rate_limit` (same-IP budget split), `cooldown_duration_ms` (429 stand-down schedule, 2h→4h→8h), `bounty_poll_interval_ms` (idle cadence + presence heartbeat), `hive_wait_tick_ms`/`hive_resweep_interval_ms` (v2.4.0 — the rate-limited hive-aware wait's tick + re-sweep cadence), `rate_bar_fills_split`/`rate_bar_split_segments` (gold fleet-slot bar), `relay_reseed_entries` (re-seed payload), `relay_toggle_outcome` (`/relay` grammar, aliased to `auto_approve_outcome`). The relay server (`relay/server.mjs`) computes none of this — it only tracks `peers`/`cooledUntil` and returns them for hosts to apply. The relay itself runs on the kernel too (canonicalization/sanitization via a vendored generated copy under `relay/_sudo`, guarded by `relay:sudo_freshness_test`) | kernel decides, hosts + relay apply | both — no host-local policy math |

## Rulings summary

| Source | Count | Status in kernel |
|--------|-------|------------------|
| Carried from v1 (canonicalized) | 4 | Unbounded BFS; no flag promotion; case-sensitive + title fallback; export/prune closure |
| New in v2 extraction | 3 | No glob safety gate; no name truncation; no scan budget / timeout (real `\|` alternation) |
| Consolidation release 2026-08-07 | 9 | Operand maxsplit; pair generation; unfilled empty-list; segment errors; crawl order/admission/uniform generations; import folds; export id remap; name sanitization; orphaned exports adopted |
| Dual `_js` implementations | 0 | Deleted; one CLI-aligned kernel only |

When `bookmarklet/trainer.js` switches to the kernel, residual items 3–6 and the
ex-gate/truncation group are the main user-visible deltas; items 1–2 are largely
pre-aligned on the current trainer tree but still consolidate onto one code path.
