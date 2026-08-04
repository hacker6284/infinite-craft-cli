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
| 3 | **Case-sensitive `get_by_name` + Python `str.title()`-style fallback** | Matches `storage.get_by_name` exact lookup and `_resolve_element` title-casing. |
| 4 | **Export/prune filter = transitive recipe closure** (`included_element_names(recipes)` only) | Matches v1.4.2 `_included_element_names` (cli.py ~2518–2547); pure orphans are export-excluded and prune targets. |
| 5 | **No fnmatch/glob safety gate** | Vendored `regex.sudo` is Thompson-NFA / Pike-VM, linear in input length — wildcard-count / consecutive-star heuristics are unnecessary. |
| 6 | **No 512-char name truncation** | Only the **query** is capped at 512; names match in full. JS `name.slice(0, 512)` existed only to bound in-browser `RegExp` cost. |
| 7 | **No scan budget / regex timeout**; only error is `"Invalid regex pattern"`; full NFA regex — real grouping `()`, quantifiers over groups, nested alternation, `^`/`$`/`\b`/`\B` assertions | Same linear-time engine; CLI `MATCH_SCAN_BUDGET` / `REGEX_TIMEOUT` and JS `REGEX_TIMEOUT_MS` guard PCRE/backtracking the NFA cannot exhibit. `regex.sudo` v0.3.0 compiles `(...)` as real (always non-capturing) grouping — `(ab)+`, `(a+)+`, nested `(x(y|z))w` — and `^`/`$` as true zero-width position assertions anywhere in the pattern, not just at branch edges. `(?:...)` is **not** separately recognized (a leading `?` inside a group with nothing preceding it to quantify is a parse error) — since every group is already non-capturing, this is a confirmed syntax gap, not a matching-correctness bug. Upstream's blanket "any `\|` is too complex" rejection, and its lack of any grouping support at all, have no counterpart here. |

### 1. Unbounded trace BFS

`compute_layers` (`craft.sudo` ~255–289) follows Python's shape: `while not
found`, break only when a layer produces no new names — no iteration ceiling.
Test `"trace_recipe unbounded depth over 200 layers"` builds `C0`…`C300` and
asserts `trace_recipe(..., "C300")` returns `Steps` with length **301**. v1's
JS-only 200-layer cap would have failed that chain.

### 2. No discovered-flag promotion on re-add

`add_element` (~188–193): if the name already exists, return `false` without
mutating emoji or `first`. Re-add with `first=true` cannot promote a stored
`first=false` (covered in `"title_case resolve get_by_name add record"`).

### 3. Case-sensitive lookup + title-case fallback

`get_by_name` is exact `e.name == name`. `resolve_element` tries the raw name,
then `title_case(strip_spaces(name))`, else synthesizes
`Element(name=title, ...)`. `title_case` (~115–128) is an ASCII approximation
of Python `str.title()` (see residual JS §5 for cross-impl disagreements).

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
`sudo/craft.sudo`'s own `regex.sudo`-level tests (`"regex non-capturing
groups"`, `"regex mid-pattern anchors"`) are the underlying oracle.

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
(`title_case` / `resolve_element`). Known input disagreements (still true;
`title_case` did not change in this port):

| input | Python / kernel `title_case` | JS `\b\w` replace |
|-------|------------------------------|-------------------|
| `3d printer` | `3D Printer` | `3d Printer` |
| `HELLO world` | `Hello World` | `HELLO World` |
| `under_score name` | `Under_Score Name` | `Under_score Name` |
| `co2 gas` | `Co2 Gas` | `Co2 Gas` (agree) |

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

- **`List<T>.sort()` only for `int` / `float`.** Sorting `List<text>` needs a
  hand-rolled insertion sort (`sort_texts` / `sorted_text_list` in `craft.sudo`)
  rather than a one-shot method. (v1 also hit the Rust cross-module `inout` bug
  on `sorting.sort_by` — see below; that is why the hand-rolled path remains.)

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
  `sorting.sort_by`) drops `&mut` and fails to typecheck. `craft.sudo` keeps
  same-module `sort_texts` instead; `sorting.sudo` may still be vendored but is
  not required for the kernel path.

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

- **OPEN Zig backend bug: cross-module generic type identity.** Confirmed
  compiler bug, not an implementer mistake. Symptom when building for Zig:

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

---

## Rulings summary

| Source | Count | Status in kernel |
|--------|-------|------------------|
| Carried from v1 (canonicalized) | 4 | Unbounded BFS; no flag promotion; case-sensitive + title fallback; export/prune closure |
| New in v2 extraction | 3 | No glob safety gate; no name truncation; no scan budget / timeout (real `\|` alternation) |
| Dual `_js` implementations | 0 | Deleted; one CLI-aligned kernel only |

When `bookmarklet/trainer.js` switches to the kernel, residual items 3–6 and the
ex-gate/truncation group are the main user-visible deltas; items 1–2 are largely
pre-aligned on the current trainer tree but still consolidate onto one code path.
