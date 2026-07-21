# Divergences between the Python and JS implementations

This file documents every place where infinite-craft-cli's Python CLI
(`src/infinite_craft_cli/`) and JS bookmarklet (`bookmarklet/trainer.js`)
disagree about kernel behavior, discovered while porting the shared logic to
sudo (`sudo/craft.sudo`). Per project decision, `sudo/craft.sudo` implements
the PYTHON behavior as the wired/provisional default for every divergence,
with the JS behavior implemented alongside as an isolated, separately-named
sibling function (suffixed `_js`) so a reversal is a local call-site change,
not a rewrite. Six divergences were confirmed (five given as candidates, one
new one found during the port).

## 1. Glob character-class (`[...]`) support

**What diverges:** Python's glob matching (`fnmatch.fnmatch`) supports full
shell-style character classes — `[abc]`, `[!abc]` negation, `[a-z]` ranges —
and even switches into glob mode for a pattern containing only `[`/`]` with
no `*`/`?`. JS's hand-rolled `globToRegex` has no character-class support at
all: it escapes every non-`*`/`?` character (including `[` and `]`) as a
literal regex character, AND only switches into glob mode when the query
contains `*` or `?` (a bracket-only pattern silently falls through to plain
substring matching in JS).

**Evidence (Python):** `src/infinite_craft_cli/cli.py:170-171`
```python
if any(c in q for c in "*?[]"):
    matches = [e for e in discoveries if fnmatch.fnmatch(e.name.lower(), q)]
```

**Evidence (JS):** `bookmarklet/trainer.js:328-347`
```js
function globToRegex(pattern) {
  let re = "^";
  for (const ch of pattern) {
    if (ch === "*") re += ".*";
    else if (ch === "?") re += ".";
    else re += ch.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }
  return new RegExp(re + "$", "i");
}
function matchElements(query) {
  const elements = getAllElements();
  if (query.includes("*") || query.includes("?")) { ... }
  // else: substring fallback — brackets never trigger glob mode
```

**User-visible meaning:** A search like `/search a[bc]d` finds `"abd"` and
`"acd"` in the Python CLI (treated as a character-class glob) but in the JS
trainer only finds elements literally containing the 6-character substring
`"a[bc]d"` (almost certainly nothing).

**Recommendation:** Implement Python's full fnmatch semantics — richer,
strictly more capable, and it's the CLI's persisted-recipe source of truth
(recipes.json), so search parity with it matters more than parity with the
lighter in-browser tool.

**Implemented as:** `glob_match` / `is_glob_trigger` (wired, Python
behavior) vs `glob_match_js` / `is_glob_trigger_js` (JS behavior, isolated).

## 2. `^` first-discovery filter scope

**What diverges:** Python peels a leading `^` off the query and restricts
matches to first-discovery elements **inside the shared matching helper**
(`_match_elements`). That means every caller of `_match_elements` —
`/search`, `/permute`, `/cross`, the `+|` operator — gets the filter for
free. JS only handles `^` inside `doSearch`; the shared `matchElements`
function has no knowledge of it at all. A `^` passed through
`/permute`/`/cross`/`+|` in JS is therefore just a literal character in the
substring/glob pattern, and those commands can never filter to
first-discoveries-only.

**Evidence (Python):** `src/infinite_craft_cli/cli.py:165-176`
```python
q = query.strip()
only_new = q.startswith("^")
if only_new:
    q = q[1:]
q = q.lower()
if any(c in q for c in "*?[]"):
    matches = [e for e in discoveries if fnmatch.fnmatch(e.name.lower(), q)]
else:
    matches = [e for e in discoveries if q in e.name.lower()]
if only_new:
    matches = [e for e in matches if e.is_first_discovery]
return matches
```

**Evidence (JS):** `bookmarklet/trainer.js:419-422` (only in `doSearch`) and
`bookmarklet/trainer.js:338-347` (`matchElements` has no `^` handling):
```js
function doSearch(query) {
  let firstOnly = false;
  if (query.startsWith("^")) { firstOnly = true; query = query.slice(1); }
  let matches = matchElements(query);
  if (firstOnly) matches = matches.filter(e => e.discovered);
  ...
}
function matchElements(query) {
  // no '^' stripping — callers other than doSearch never get the filter
  if (query.includes("*") || query.includes("?")) { ... }
  const lower = query.toLowerCase();
  return elements.filter(e => e.text.toLowerCase().includes(lower));
}
```

**User-visible meaning:** `/search ^` (or `/search ^steam`) works as a
first-discovery filter in both tools, but `/permute ^*` / `/cross ^a ^b` /
the `+|` operator only honor the filter in Python. In JS those flows treat
`^` as a literal character and usually match nothing.

**Recommendation:** Keep the filter inside the shared matcher (Python), so
every query surface has consistent first-discovery semantics. Isolating the
filter into `filter_first_discoveries` makes a future move to JS's call-site
scope a one-line relocation.

**Implemented as:** `match_elements` (wired) vs `match_elements_js`
(isolated); the `^`-filtering itself is further isolated into
`filter_first_discoveries` so moving where it's called from is the entire
diff needed to flip this divergence.

## 3. `/recipe` BFS lineage depth cap

**What diverges:** Both implementations BFS over recorded recipes to
reconstruct a crafting lineage, but JS hard-caps the walk at 200 layers while
Python runs an unbounded `while not found` that only stops when a layer makes
no progress (or the target is reached).

**Evidence (Python):** `src/infinite_craft_cli/cli.py:225-241`
```python
while not found:
    new_this_layer = {}
    for result_name, pairs in recipes.items():
        ...
    if not new_this_layer:
        break
    for name, recipe in new_this_layer.items():
        parent[name] = recipe
        visited.add(name)
```

**Evidence (JS):** `bookmarklet/trainer.js:448`
```js
for (let depth = 0; depth < 200 && !found; depth++) {
  const layer = [];
  for (const [resultName, pairs] of Object.entries(recipeIndex)) {
    ...
  }
  ...
}
```

**User-visible meaning:** For a lineage requiring more than 200 sequential
crafting layers, JS's `/recipe` reports "cannot trace" even though the
lineage is fully knowable from the recorded recipes; Python always finds it
if it exists.

**Recommendation:** Prefer Python's unbounded walk (with the natural
"no progress this layer" termination). A 200-layer cap is an arbitrary
browser-safety cutoff that silently misreports long-but-valid lineages; the
kernel has no host cost model that would justify baking it in.

**Implemented as:** `compute_layers(target, recipes, max_layers)` shared
helper; `trace_recipe` (wired) calls it with a large sentinel (no practical
cap); `trace_recipe_js` calls it with `200`.

## 4. Discovery-flag upgrade on re-add

**What diverges:** When an element name is already known and `add` is called
again with `first`/`discovered = true`, Python leaves the existing record
completely untouched (and returns "not newly added"). JS upgrades the
existing entry's discovery flag in place from false→true, still returning
false (only a brand-new insert returns true).

**Evidence (Python):** `src/infinite_craft_cli/storage.py:69-76`
```python
def add(self, *, name: str, emoji: str | None = None,
        is_first_discovery: bool | None = None) -> Element | None:
    """Add an element. Returns the Element if newly added, None if already exists."""
    if name in self._index:
        return None
    elem = Element(name=name, emoji=emoji, is_first_discovery=is_first_discovery)
    self._elements.append(elem)
    self._index[name] = elem
    self._save()
    return elem
```

**Evidence (JS):** `bookmarklet/trainer.js:201-218`
```js
function addElement(text, emoji, discovered) {
  const existing = _nameIndex[text.toLowerCase()];
  if (existing) {
    // Update discovery flag if newly discovered
    if (discovered && !existing.discovered) {
      existing.discovered = true;
      putItem(existing);
    }
    return false;
  }
  ...
  return true;
}
```

**User-visible meaning:** Re-encountering an element as a first discovery
after it was previously stored without the flag (e.g. as a bare ingredient)
promotes it in the JS trainer but never in the Python CLI. Export payloads
and first-discovery search filters (`^`) then disagree across tools for the
same underlying history.

**Recommendation:** Keep Python's never-update-on-readd policy as the wired
path — it is simpler, idempotent, and matches the persistence layer's
"insert or ignore" shape. The JS upgrade is recoverable via an explicit
`_js` sibling if product wants the promote-on-rediscovery behavior later.

**Implemented as:** `add_element` (wired, Python — no upgrade, ever) vs
`add_element_js` (isolated, upgrades in place).

## 5. Case sensitivity of name lookup + title-casing algorithm

**5a — lookup:** Python `storage.get_by_name` (`storage.py:65-66`) is an
exact, case-sensitive dict lookup; JS `getByName` (`trainer.js:188-190`)
lowercases both the stored index key and the query. Since `_resolve_element`
falls back to a title-cased lookup only when the exact lookup misses, this
makes the title-casing algorithm itself load-bearing in Python in a way it
mostly isn't in JS (JS's case-insensitive first lookup already catches most
casing variants before title-casing is even consulted).

**Evidence (Python):** `src/infinite_craft_cli/storage.py:65-66` and
`src/infinite_craft_cli/cli.py:99-110`
```python
def get_by_name(self, name: str) -> Element | None:
    return self._index.get(name)

def _resolve_element(storage, name: str):
    found = storage.get_by_name(name)
    if found is not None:
        return found
    title = name.strip().title()
    if title != name:
        found = storage.get_by_name(title)
        if found is not None:
            return found
    return Element(name=name.strip().title())
```

**Evidence (JS):** `bookmarklet/trainer.js:188-199`
```js
function getByName(name) {
  return _nameIndex[name.toLowerCase()] || null;
}
function resolveElement(name) {
  const el = getByName(name);
  if (el) return el;
  const titled = name.replace(/\b\w/g, c => c.toUpperCase());
  const el2 = getByName(titled);
  if (el2) return el2;
  return { text: name.trim().replace(/\b\w/g, c => c.toUpperCase()),
           emoji: "", discovered: false };
}
```

**5b — title-casing algorithm:** Python uses `str.title()`
(`cli.py:105,110`); JS uses `name.replace(/\b\w/g, c => c.toUpperCase())`
(`trainer.js:195,198`). These are NOT the same algorithm. Verified against
real Python 3 / Node output:

| input | Python `.title()` | JS `\b\w` replace | agree? |
|---|---|---|---|
| `baker's dozen` | `Baker'S Dozen` | `Baker'S Dozen` | yes |
| `co2 gas` | `Co2 Gas` | `Co2 Gas` | yes |
| `3d printer` | `3D Printer` | `3d Printer` | **no** |
| `HELLO world` | `Hello World` | `HELLO World` | **no** |
| `under_score name` | `Under_Score Name` | `Under_score Name` | **no** |

Python's algorithm treats any non-alphabetic character (digits, `_`,
punctuation) as a word boundary AND lowercases every non-first letter of a
word; JS's regex treats digits and `_` as word characters (`\w`, so no
boundary there) and NEVER lowers a letter it doesn't touch — it only
uppercases the one character immediately following a true boundary.

**Recommendation:** Implement Python's `.title()` semantics (ASCII
approximation — see "sudo friction" below) as the wired path, matching
`get_by_name`'s exact-case-sensitivity choice above (both come from the
Python CLI, the persistence source of truth).

**Implemented as:** `get_by_name` / `title_case` (wired, Python) vs
`get_by_name_js` / `title_case_js` (isolated, JS) — composed into
`resolve_element` (wired) vs `resolve_element_js` (isolated).

## 6. Export transitive closure (found during this port — not one of the
five original candidates)

**What diverges:** Python's `do_export` (`cli.py:701-777`) computes a
transitive closure over recipe ingredients before exporting: only base
elements, elements with their own recorded recipe, and anything transitively
referenced as an ingredient by an included recipe are exported; "pure
orphans" (no recipe, referenced by nothing) are excluded. JS's `doExport`
(`trainer.js:767-787`) has NO such logic — it maps every item in `_items`
directly into the export payload, unconditionally:
```js
const exportItems = _items.map(item => { ... });
```

**Evidence (Python):** `src/infinite_craft_cli/cli.py:712-732,738-740`
```python
included = set(_BASE_ELEMENTS)
for elem in discoveries:
    if elem.name in recipes:
        included.add(elem.name)

changed = True
while changed:
    changed = False
    for name in list(included):
        if name in recipes:
            for a, b in recipes[name]:
                if a not in included:
                    included.add(a)
                    changed = True
                if b not in included:
                    included.add(b)
                    changed = True
...
for elem in discoveries:
    if elem.name not in included:
        continue
```

**User-visible meaning:** exporting from the JS trainer always round-trips
every discovered element (including ones with no known recipe, that were
never `/fill`ed and aren't needed by anything); exporting from the Python
CLI silently drops such orphans (with an on-screen count) unless you `/fill`
them first.

**Recommendation:** Implement Python's closure — it's what the "export
transitive closure" deliverable in this port's task spec explicitly asked
for, and it's the more conservative, self-consistent choice (every exported
recipe's ingredients are guaranteed present in the same export).

**Implemented as:** `export_elements` / `export_included_names` (wired,
Python) vs `export_elements_js` / `export_included_names_js` (isolated, JS
— no filtering).

## sudo language friction

Notes on where the sudo language (v0.1, per `spec/language.md`) made this
port more awkward than it would be in Python or JS:

- **No cross-directory stdlib import path.** `import strings` resolves only
  to a `.sudo` file in the SAME directory as the importing file
  (`sudoc/crates/types/src/lib.rs`, `load_modules`) — there is no configured
  stdlib search path. This port has to vendor verbatim copies of
  `strings.sudo` and `sorting.sudo` alongside `craft.sudo` rather than
  import them from `sudocode/stdlib/` directly, which will drift out of
  sync with the real stdlib silently if the stdlib is ever updated.
- **No positional tuple field access (`t.0`/`t.1`).** Only named-field
  record access works; every place this port needed to pull one field out of
  a tuple, it had to add an extra `a, b = t` destructuring-assignment
  statement (and could not do it inline in a `for` loop header at all — the
  `for a, b in ...` form is reserved for Map key/value iteration only). For
  anything with 2+ fields that's read more than once, a `record` is more
  ergonomic than a tuple in sudo, even though `spec/language.md` presents
  tuples as the lightweight option.
- **`List<T>.sort()` only covers `T = int` / `T = float`.** Any `List<text>`
  (`Map<text,_>.keys()`, `Set<text>.items()` — both common here, since the
  whole kernel keys everything by element name) needs the generic
  `sorting.sort_by` plus a hand-written comparator, imported from a second
  stdlib module. This is a reasonable design (`sort()` stays simple, `sort_by`
  generalizes) but it means "just sort these strings" is two imports and a
  three-line wrapper function instead of one method call.
- **Function values are top-level refs only (no closures, no module-qualified
  pass-through).** The task requires a local `lex_less_local` wrapper around
  `strings.lex_less` before passing it to `sorting.sort_by`. That matches the
  language rule that function values are references to top-level functions
  only — a qualified `strings.lex_less` is not a safe function-value form, so
  the wrapper is kept rather than risking a compile error at the call site.
- **No built-in int-to-text conversion.** The depth-cap lockstep test needs
  to synthesize 251 element names `C0`…`C250` in a loop. sudo has no
  `str(i)` / `itoa` builtin, so `craft.sudo` carries a small hand-rolled
  base-10 `int_to_text` helper (digit extraction via `mod 10` / floor
  division). Fine for a pure kernel, but another thing every non-trivial
  test suite will reimplement.
- **No Unicode casing tables.** `title_case`/`title_case_js`/`strip_spaces`
  in this port are ASCII-only approximations of Python's `str.title()` /
  `str.strip()` and JS's `\w`/`trim()`, which are full-Unicode-aware in their
  real runtimes. `stdlib/strings.sudo`'s own `to_upper`/`to_lower` are
  likewise ASCII-only. For an app whose element names are arbitrary
  player-submitted text, this is a real fidelity gap, not just a style
  choice — flagged here rather than silently narrowed.
- **Explicit parens required for mixed/`not`-around-comparison precedence.**
  Patterns like `not (title == name)` and careful `and`/`or` grouping show up
  throughout; the language refuses to guess where Python and C disagree, which
  is correct but noisier than either source language.
- **The Swift backend cannot compile a `match` arm whose only statement is
  `skip`.** `skip` compiles to nothing in the Swift backend
  (`sudoc/crates/backend_swift/src/code_gen.rs:353`), producing an empty
  `case` body, which Swift's `switch` rejects
  (`'case' label in a 'switch' must have at least one executable statement`)
  — even though `spec/language.md` §5.3 documents `skip` as precisely the
  correct idiom for a deliberately-empty match arm, and every other backend
  (py/c/js/rs/zig/hs) accepts it. Worked around here by writing `assert
  true` instead of `skip` in the one place this file needed an empty arm;
  flagging because the workaround shouldn't be necessary per the language
  spec as written.
- **The Zig backend cannot compile a `match` arm that binds a variant
  payload it never uses.** `backend_zig/src/lib.rs`'s `emit_match` (~line
  1280) unconditionally emits a `const <binder> = ...;` for every named
  payload in a `case Variant(a, b, ...)` pattern, whether or not the arm's
  body reads it — and Zig treats an unused local `const` as a hard compile
  error. sudo's `match` grammar requires full-arity binder names for every
  variant pattern (there's a whole-pattern `_` wildcard, but no per-field
  `_` inside a variant pattern's binder list per §6.3's `pattern` grammar),
  so a common, idiomatic "this branch is unreachable here" arm like
  `case IsBase(n): assert false` cannot be written as-is if it targets Zig —
  every other backend (py/c/js/rs/swift/hs) tolerates it. Worked around
  throughout this file's tests by folding the otherwise-unused binder into
  the (still-always-false) assertion itself, e.g. `assert n.length < 0`
  instead of a bare `assert false`. A per-field `_` binder (or an
  unused-binder suppression in the Zig backend's own codegen, mirroring
  the `_ = &name;` idiom already used elsewhere in that backend for
  never-mutated `var`s) would remove the need for this workaround.
- **The Rust backend cannot correctly compile a cross-module call to a
  function with an `inout` parameter.** `backend_rs/src/lib.rs`'s
  `CallFunc` codegen (~line 797) looks up the callee's parameter signature
  via `self.m.func(name)`, which only searches the CURRENT module's own
  function list (`ir/src/lib.rs:336`) — a qualified cross-module call name
  (e.g. `sorting.sort_by`) never matches, so the lookup silently returns
  `None` and the backend falls back to its by-value ("no inout params")
  codegen path, dropping the `&mut` the callee actually requires
  (`error[E0308]: mismatched types ... expected &mut Vec<...>, found
  Vec<...>`). This blocked using `stdlib/sorting.sudo`'s generic
  `sort_by(items: inout List<T>, less)` from `craft.sudo` to sort
  `List<text>` (needed for deterministic `Map<text,_>.keys()` /
  `Set<text>.items()` iteration, per spec §12) — worked around here with a
  same-module, non-generic `sort_texts` insertion sort instead (which still
  calls `strings.lex_compare` cross-module, fine, since that parameter
  isn't `inout`). `sorting.sudo` is still vendored in this directory but no
  longer imported/used, since ANY cross-module call into its `inout`-taking
  `sort_by` hits this bug regardless of element type.
- **Record/enum text fields lose boundary intent when the record/enum type
  itself appears in an `export func` signature.** A newly-discovered sudo
  toolchain limitation (found during concurrent host-adapter work on this
  project): `text` (`List<int>`) fields are fine as `export func`
  parameters/returns when they appear top-level or nested directly under
  `List`/`Map`/`Tuple`/`Option` — but a `text` field reached *through a
  named `record` or `enum` type* (e.g. `Element.name` when `List<Element>`
  is an export parameter type) degrades to a raw code-point array at the JS
  host boundary instead of a native string. Workaround used throughout this
  file: keep `record Element` / `record RecipeStep` / `enum RecipeResult`
  for the INTERNAL engine (regular, non-`export` functions, full
  expressiveness, used directly by every `test` block), and add a separate
  "boundary adapter" layer of small `export func`s whose signatures are
  built entirely out of tuples/primitives (`(text, text, bool)` for an
  element, `(int, text, List<(text,text,text)>)` for a `RecipeResult`) that
  convert at the edge and delegate to the internal functions. This is a
  reasonable, low-overhead pattern once you know to reach for it, but it
  means every record-shaped export needs a hand-written, easy-to-typo
  tuple-encoding/decoding pair — a language- or tooling-level "derive a
  boundary-safe projection of this record" facility would remove real
  boilerplate here.

---

## Rulings (2026-07-20, project owner)

All six divergences ruled in favor of the **Python** behavior — the wired
paths stand unchanged: (1) fnmatch character classes; (2) `^` filter in the
shared matcher; (3) unbounded BFS with no-progress termination; (4) insert-
or-ignore, no flag promotion on re-add; (5) exact case-sensitive lookup with
`.title()` fallback; (6) export as transitive closure. The `_js` siblings
are retained as executable documentation of the pre-port JS behaviors and
are removed when trainer.js switches to the kernel.
