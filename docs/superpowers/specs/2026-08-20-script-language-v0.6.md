# Infinite Craft Script Language — Formal Specification (v0.6)

Supersedes v0.5. Every change from v0.5 was settled in design review 2026-08-19/20;
decision rationale is recorded inline as **[D-n]** notes. This document is the
implementation contract for both hosts (Python CLI and browser trainer).

---

## 0. Entry points **[D-1]**

- **The script language is the REPL line grammar.** Every submitted line that
  does not begin with a slash command is parsed and executed as a script.
  Legacy shorthands (`A + B`, `A ++ B`, `Q * Q`) are degenerate scripts and —
  with the exceptions listed in §9 (migration) — behave as before.
- **Slash commands remain line-level and are NOT script statements.** A `/`
  command inside a script is a parse error. The ib lane (`/import`, `/fill`,
  `/prune`), mode toggles (`/auto`, `/target`), and display commands are
  interactive-only surface.
- **`/script <path>`** executes a stored script file (recommended extension:
  `.ice`). Bare `/script` in the trainer opens a file picker (like bare
  `/import`). Non-interactive CLI: `infinite-craft script "<source>"` and
  `infinite-craft script -f <path>`.
- A script executes in the **pair lane** (queued, one at a time, shown in job
  chrome), like any bulk command — **except pure, loop-free scripts**
  (v0.6.2): a script whose parse shows no mutating operation and no
  until/while/for-each runs immediately, interleaving with a running
  command exactly like `/search` always has. Loops always queue, even pure
  ones: the queue's cancel machinery is the only brake on an unbounded
  pure loop.

## 1. Lexical elements

```
ident    ::= [a-zA-Z_][a-zA-Z0-9_]*
number   ::= [0-9]+          // legal only inside condition arithmetic (§6)
string   ::= '"' [^"]* '"' | "'" [^']* "'"    // element reference (§4.3)
pattern  ::= the existing CLI query language (substring, fnmatch * ? [],
             /regex/, ! and !q exclusion, ^ and ^q first-discoveries)
```

### 1.1 Tokenization — the standalone/attached rule **[D-2]**

*Attached characters belong to the pattern; standalone tokens belong to the
grammar.*

- **Binary operators are standalone tokens**: an operator character sequence
  is an operator only when delimited by whitespace (or structure) on both
  sides: `+ - * / % & ++ , @ -> ~ ? : := && || > < >= <= == !=`.
- **A pattern token is greedy**: it runs across spaces and embedded
  `* ? [ ] / ! ^ - : .` until a standalone operator token or a structural
  delimiter. `mountain range + ship` is two patterns and one operator;
  `jack-in-the-box` is one pattern.
- **Postfix `*`, `**`, `!` attach with no whitespace, and only immediately
  after `)` or `]`**: `(fire*)*`, `[]!`.
- **Structural (reserved) characters** — never pattern material unquoted:
  `( ) { } ; , @ " ' #`. Additionally: `[` / `]` are structural only when
  standalone (§1.2); `|` is structural except inside `/…/` regex delimiters.
- **`@ident`** (attached) is a single token: the for-each binder (§7.1).
- **Newlines are whitespace to the grammar.** `;` is the only statement
  separator; trailing `;` and empty statements are tolerated. **[D-3]**
- **`#` begins a comment stripped by the lexer through physical end-of-line**
  (or end of input). Comments never reach the parser, so this coexists with
  newlines-as-whitespace. No block comments. **[D-3]**

### 1.2 Brackets **[D-4]**

- **New-elements set syntax requires standalone brackets**: `[ expr ]` (inner
  whitespace mandatory) and the exact token `[]`.
- **Attached brackets are pattern material in every position, including
  leading**: `[bc]at`, `[fw]*`, `mu[dg]`, `fire[0-9]` all retain their fnmatch
  meaning. (`[fire*]` — attached — is therefore a character class, not a
  new-set; no hint is printed.)

## 2. Grammar (EBNF)

```ebnf
script     ::= stmt (";" stmt)*

stmt       ::= expr
             | for_each
             | until_stmt
             | while_stmt
             | if_stmt

for_each   ::= expr ("@" | AT_IDENT) body        // AT_IDENT = "@"ident, one token
until_stmt ::= body "->" cond                    // do-until: run, then test
while_stmt ::= body "~"  cond                    // while: test, then run
if_stmt    ::= cond "?" body ":" body

body       ::= stmt | "{" script "}"

cond       ::= or_cond                           // statically pure (§6.3)
or_cond    ::= and_cond ("||" and_cond)*
and_cond   ::= atom_cond ("&&" atom_cond)*
atom_cond  ::= comparison | expr | "(" cond ")"  // set: true when non-empty
comparison ::= num_expr cmp_op num_expr
cmp_op     ::= ">=" | "<=" | ">" | "<" | "==" | "!="
num_expr   ::= num_term (("+" | "-") num_term)*
num_term   ::= num_atom (("*" | "/" | "%") num_atom)*
num_atom   ::= "|" expr "|" | number | "(" num_expr ")"

expr       ::= assignment
assignment ::= ident ":=" expr | union           // := right-associative
union      ::= additive ("," additive)*          // union operands: see §5.2
additive   ::= multiplicative (("+" | "-") multiplicative)*
multiplicative ::= crawl (("*" | "/" | "%" | "&") crawl)*
crawl      ::= postfix ("++" postfix)*
postfix    ::= primary
             | "(" expr ")" ("*" | "**" | "!")   // permute / permutate / exhaust
             | postfix count                     // take: (expr)100  (expr)(num_expr)
             | postfix count "?"                 // sample: (expr)100?  (expr)(num_expr)?
             | postfix "?"                       // shuffle: (expr)?
             | "[" expr "]"                      // new-elements of expr (standalone brackets)
             | "[]"                              // new-elements register (§5.4)
count      ::= DIGITS                            // attached, no whitespace
             | "(" num_expr ")"                  // attached; full cond-grade arithmetic
primary    ::= pattern                           // shape rule: §4.2
             | string                            // quoted element reference
             | ident                             // variable (then element) §4.3
             | "_"                               // for-each loop element (§7.1)
             | "^(" expr ")"                     // first-discoveries filter
             | "(" expr ")"
```

Operator precedence (tightest first): postfix forms; `^()`; `++`;
`* / % &`; `+ -`; `,`; `:=`; control structures are statement-level.
`+|` **is removed** (see §9). **[D-5]**

## 3. Core model

- **A set value is an immutable, ORDERED snapshot** of element identities
  taken at evaluation time: match order for queries, left-then-right for
  unions, producer order for products; deduplicated by name. All iteration
  and display orders are deterministic and identical across hosts. **[D-6]**
- **A pattern is lazy**: it re-evaluates against the current global discovery
  set each time it is read. `:=` exists to snapshot. **[D-6]**
- **Execution environment**: each script execution (one REPL line, one
  `/script` run) gets a fresh variable environment; walrus bindings do not
  survive it. `{ … }` blocks and `@` bodies nest lexical child scopes.
  The `[]` register (§5.4) is the sole cross-execution state. **[D-7]**

## 4. Primaries

### 4.1 Queries
A pattern containing any query metacharacter (`* ? [ ] / ! ^`) evaluates with
today's matching rules against the current global set. May be empty.

### 4.2 Bare words — the shape rule **[D-8]**
A pattern with **no metacharacters** is an **element reference**: resolved by
the existing chain (exact → stripped → title-case), yielding a singleton.
**Runtime error if nothing resolves** (typos fail loud, as the old combine
did). Substring matching is spelled explicitly: `*fire*` or `/fire/`.

### 4.3 Variables and quoted strings **[D-9]**
A bare ident resolves **variable-first** (innermost scope), then as an
element reference. A **quoted string is always an element reference** —
never a variable, never a pattern; metacharacters inside quotes are inert.
Use quotes for element names containing reserved characters and to reach an
element shadowed by a binding.

### 4.4 First-discoveries
`^(expr)` filters any set expression to first discoveries. The lexical form
`^pattern` (inside a query token) is unchanged; the two coexist. **[D-10]**

## 5. Operations

### 5.1 The uniform value rule **[D-11]**
**Every mutating operation's value is the set of elements it PRODUCED** (all
non-Nothing combine outputs, whether or not new to the discovery set):

| Expression | Operation | Value |
|---|---|---|
| `A + B` | combine (singletons only) | result singleton, or empty on Nothing |
| `A * B` | cross (A×B, same-name pairs skipped, symmetric dedup) | products |
| `A ++ B` | crawl: pool seeded with A ∪ B; each generation tries every untried unordered pool pair INCLUDING self-pairs (legacy `/crawl` semantics); a generation that adds no element to the pool ends the crawl (v0.6.1 correction — the earlier "seeded from A×B" wording was wrong) | all products across generations |
| `(S)*` | permute (upper triangle of S) | products |
| `(S)**` | permutate (permute rounds until a round adds nothing) | products across rounds |
| `(S)!` | exhaust (each of S × all discoveries) | products |

Pipelines follow: `fire + water + earth` chains combines;
`(fire* * water*) * earth*` crosses the products. To keep the *input* set,
snapshot it first (`before := fire*`).

### 5.2 Pure operators
- `A , B` — **union** (n-ary, ordered, deduped). **A `,` operand that is a
  mutating expression must be parenthesized** (parse-time error otherwise):
  `a* , (b* * c*)`. **[D-12]**
- `A - B` — difference. `A & B` — intersection.
- `A / B` — elements of A having a **known recipe** with some element of B.
  `A % B` — its complement (A minus `A / B`). Recipe-index-based; no API
  calls, no mutation, deterministic. **[D-13]**
- **Take / sample / shuffle (v0.6.2)** — postfix forms attached to `)`,
  `]`, `[]`, or another postfix, mirroring the `*`/`**`/`!` family:
  `(S)n` = first `min(n, |S|)` elements in set order; `(S)n?` = `n`
  uniformly random elements (shuffle-then-take, without replacement);
  `(S)?` = the whole set shuffled. Counts are attached digits or an
  attached parenthesized numeric expression (`(fire*)(|water*| - 3)`)
  with the condition sublanguage's grammar; count expressions are
  statically pure (same check as conditions); `n ≤ 0` yields the empty
  set. All three are non-mutating (mutation flag inherits from the
  inner expression). Randomness is host-seeded: the kernel is
  deterministic given the seed (hosts pass a clock-derived seed that
  advances per evaluation, so loop iterations resample); under test,
  fixed seeds pin exact outputs. A spaced `(expr) (5)` or word-adjacent
  `a*(5)` group remains a juxtaposition error — counts attach.
- `A + B` with a non-singleton operand is a **runtime error** ("use `,` to
  collect or `*` to cross"). `+` never unions. Self-pairs (`fire + fire`)
  are legal. **[D-14]**

### 5.3 Mutating operators
All operations in §5.1 mutate the global discovery set through the existing
pipeline: pair cache, rate limiter, history, recipe recording, live page
sync, `(new)` echo. Pair generation reuses the existing kernel functions
(`permute_pairs`, `cross_pairs`, `exhaust_pairs`, `crawl_generation_pairs`)
with prioritization as today.

### 5.4 New-elements sets **[D-15]**
- `[ expr ]` — the set of elements newly added to the global set during that
  evaluation (union across all mutations inside; nests correctly). Around a
  pure expression: the empty set.
- `[]` — a **session-global register** holding the new-elements set of the
  most recent completed mutating **AST-node evaluation** (a permutate or
  crawl counts once, as a whole; internal rounds do not update it). Survives
  across statements and REPL lines. Pure operations never touch it; a
  mutating op that adds nothing sets it to empty.
- Positioning: `[ expr ]` is the scripting form; `[]` is the interactive
  "what did that just make?" register (shell-`$?` analogy).

## 6. Conditions **[D-16]**

- A cond is: a comparison over numeric expressions (sizes `|expr|`, number
  literals, `+ - * / %` arithmetic, both sides full expressions, `!=`
  included); or a set expression (true when non-empty); or a bare numeric
  expression (true when nonzero); combined with `&&`, `||`, parentheses.
  There is no boolean NOT.
- Numbers are legal **only** inside condition arithmetic.
- **Conditions are statically pure**: any mutating operator (postfix forms,
  `*`, `++`, `+`) inside a cond is a **parse-time error**. The check is
  exact — `,` union, `- & / %`, sizes, and variables are all fine. Idiom:
  mutate in the body, walrus the measurement, test the variable
  (`{ n := [ (b*)* ] } -> |n| < 2`).

## 7. Control structures

### 7.1 For-each **[D-17]**
`expr @ body` and `expr @name body`. Evaluates `expr` once to an ordered
snapshot; executes `body` once per element, in set order. `@name` binds
`name` in a child scope; anonymous `@` binds `_`. Referencing `_` outside a
for-each is a parse-time error. For-each is exhaustive application: **no
break/continue**; per-element early exit belongs to an inner `->`/`~` loop.

### 7.2 Loops **[D-18]**
- `body -> cond` — **do-until**: run body, then test; repeat until cond true.
  At least one execution.
- `body ~ cond` — **while**: test first; body only if cond true. Zero
  executions possible. (Docs: "`->` runs then checks; `~` checks then runs.")
- **No iteration caps.** Loops run until cond, cancellation, or error.
  (Correspondingly, `/permutate`'s MAX_PERMUTATE_ROUNDS cap is removed from
  both hosts: permutate stops only on a zero-new round.) **[D-19]**
- Conditions are checked **only between body executions — never inside an
  atomic bulk operation**. Mid-batch stopping remains `/target`'s job.
- **A loop owns one scope, shared by its body and its condition** (v0.6.1
  clarification, from stress-testing): bindings made by the body — braced
  or not — are visible to the test, so `{ n := [ (b*)* ] } -> |n| < 2`
  works as §6 documents. Bindings persist across iterations (rebound each
  pass) and are dropped when the loop exits.

### 7.3 Ternary
`cond ? body : body`. Else branch required. Value: the executed branch's
value.

### 7.4 Non-interactive execution (v0.6.1 clarification)
Without a TTY there is no y/n: bulk operations over the warn threshold
announce their pair count and proceed; script parse errors and aborts exit
non-zero. Exception: a `/target` hit still reads its y/n from stdin (a
piped line may be consumed as the answer); at stdin EOF the run resolves
as stopped rather than hanging. SIGINT cancels cleanly (exit 130) with
partial progress kept.

## 8. Execution, safety, output

### 8.1 Parse before execute **[D-20]**
The entire script is parsed and all static checks run (cond purity, comma
parenthesization, bracket rules, `_` placement) before statement 1 executes.
Nothing runs in a script with a syntax error.

### 8.2 Runtime errors **[D-20]**
A statement-level runtime error (unresolved element reference, `+` arity,
a lone combine failing after kernel retries, unbound variable) **aborts the
whole script**; remaining statements and loop iterations are skipped. State
already reached (discoveries, variables, `[]`) is kept — the CLI never lies
about the save. **Pair-level failures inside a bulk op remain non-fatal**
(counted in the summary, as today).

### 8.3 Cancellation **[D-21]**
Stop/Esc (trainer) and Ctrl-C (CLI) abort the whole script. Checked between
pairs (existing), between statements, and between loop iterations (new).

### 8.4 Confirms **[D-22]**
Scripts inherit interactive behavior uniformly: every mutating op over the
bulk threshold pauses for `confirm [y/n]`; `/auto` skips exactly those;
`/target` hits pause inside bulk ops via the existing machinery. Scripts are
not a separate trust domain.

### 8.5 Echo **[D-23]**
1. A pure statement echoes its value in the existing `/search` list format,
   uncapped.
2. A mutating statement's operation output (combine echoes, bulk summaries)
   stands alone; no additional value echo.
3. `name := expr` prints a dim `name = N elements` acknowledgment —
   suppressed inside loop and for-each bodies, where it would emit one
   line per iteration (v0.6.1).
4. Loops close with a dim status: `loop: condition met after N iterations` /
   `~ loop: condition false, body skipped`.
5. `@` adds no output of its own.
No advisory "did you mean" hints anywhere. **[D-24]**

## 9. Migration (breaking changes → 2.0.0) **[D-25]**

1. **`+|` is removed.** `A +| B` is a parse error suggesting `*`. `/with`
   (slash command) is unchanged.
2. **Bare words are element references everywhere.** Old `Q * Q` and
   `A +| Q` treated bare operands as substring queries; write `*q*` for that.
3. **Bare pattern lines now print matches** (old: usage error).
4. `A + B + C` now chains combines (old: attempted an element named
   "B + C" and failed).
5. `/permutate` no longer stops at 50 rounds.

## 10. Implementation architecture **[D-26]**

- **Kernel (`craft.sudo`)**: tokenizer, parser → AST, all static checks, and
  pure evaluation helpers (pattern evaluation, set algebra, cond evaluation,
  products/new-set bookkeeping decisions). Covered by lockstep tests on both
  backends.
- **Hosts**: a thin statement driver performing effects via the existing
  bulk machinery (`runPairsInner` / `_combine_pairs`, pair cache, rate
  limiter, confirm flow). No host-side language semantics.
- **Both hosts ship in the same release** (the line grammar is shared kernel
  property; a one-host language fork is a DIVERGENCES.md violation by
  construction).
- **Version: 2.0.0.**

## 11. Deferred (explicitly out of v1)

Broadcast `@set` in expression position; break/continue; loop cap overrides;
slash commands inside scripts; `/vars` listing; block comments; explicit
binder syntax beyond `@ident`; try/catch or continue-on-error modes.

---

## Appendix A — Examples (v0.5 examples, corrected)

```text
# 1. Wildcard query (echoes matches)
fire*

# 2. Combine two elements (bare words = element references)
fire + water

# 3. Permute a wildcard query
(fire*)*

# 4. Permute passes until a pass yields fewer than 3 new elements
(fire*)* -> |[]| < 3

# 5. Work the fire elements until there are 12 steams (may overshoot
#    within a pass; overshoot-sensitive stopping is /target's job)
(fire*)* -> |steam*| >= 12 ; water* * earth*

# 6. Snapshot + new-elements set (one line, semicolons)
before := ^(fire* / water*) ; (before)* ; [] * (earth* / before)

# 7. Exhaust metals with no known fire recipe
(metal* % fire*)!

# 8. Pipeline
targets := ^(fire* / water*) ; (targets)* -> |[]| < 2 ;
(steam* , targets) * (^(earth*) / fire*)

# 9. Exhaust only what the permute just created
(base*)* ; []!

# 10. For-each with binder: exhaust each fire element, then report
fire* @f { (f)! } ; |[]|

# 11. Union list with an embedded (parenthesized) mutation
x := a* , (b* * c*)

# 12. While under budget: permute only while the save is small
{ (fire*)* } ~ |!| < 2000
```
