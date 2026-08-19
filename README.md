# infinite-craft-cli

[![PyPI](https://img.shields.io/pypi/v/infinite-craft-cli)](https://pypi.org/project/infinite-craft-cli/)
[![Downloads](https://img.shields.io/pypi/dm/infinite-craft-cli)](https://pypi.org/project/infinite-craft-cli/)
[![License](https://img.shields.io/badge/License-MIT-red?labelColor=black)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10+-blue)](https://python.org)
[![Tests](https://github.com/hacker6284/infinite-craft-cli/actions/workflows/test.yml/badge.svg)](https://github.com/hacker6284/infinite-craft-cli/actions/workflows/test.yml)
[![Publish](https://github.com/hacker6284/infinite-craft-cli/actions/workflows/publish.yml/badge.svg)](https://github.com/hacker6284/infinite-craft-cli/actions/workflows/publish.yml)
[![Chrome Web Store](https://img.shields.io/chrome-web-store/v/gaonnldioeddnfopgejohbhoajoagbnd)](https://chromewebstore.google.com/detail/infinite-craft-trainer/gaonnldioeddnfopgejohbhoajoagbnd)

Interactive CLI for [Infinite Craft](https://neal.fun/infinite-craft/) — combine elements from the terminal. Also available as a [browser extension](https://chromewebstore.google.com/detail/infinite-craft-trainer/gaonnldioeddnfopgejohbhoajoagbnd) and [web trainer](https://hacker6284.github.io/infinite-craft-cli/).

Originally built on [infinite-craft](https://github.com/sqdnoises/infinite-craft) by [@sqdnoises](https://github.com/sqdnoises). As of v1.0, uses [curl_cffi](https://github.com/lexiforest/curl_cffi) directly.

## Installation

```bash
pip install infinite-craft-cli
```

### Browser extension

Install the [Infinite Craft Trainer](https://chromewebstore.google.com/detail/infinite-craft-trainer/gaonnldioeddnfopgejohbhoajoagbnd) from the Chrome Web Store — it loads automatically on neal.fun/infinite-craft and fetches the current trainer (`trainer.min.js`) from [GitHub Pages](https://hacker6284.github.io/infinite-craft-cli/) with `cache: 'no-store'`, so feature updates ship without waiting for a Chrome Web Store release. The extension manifest version (`extension/manifest.json`) is independent of the Python CLI package version (git tags). See [PRIVACY.md](PRIVACY.md) for the remote-script trust model. Works in Edge, Brave, and other Chromium browsers.

**Manual QA (extension):** After changes to `extension/loader.js`, load the unpacked extension on live [neal.fun/infinite-craft](https://neal.fun/infinite-craft) and confirm the trainer UI appears and IndexedDB-backed commands work (e.g. `/list`).

For other install methods (console snippet, userscript), see the [web trainer page](https://hacker6284.github.io/infinite-craft-cli/).

## Usage

### Interactive mode

```bash
infinite-craft
```

This opens a REPL where you can combine elements, search discoveries, and more:

```
=== Infinite Craft CLI ===

craft> Water + Fire
  💨 Water + 🔥 Fire = 💨 Steam

craft> /search steam
  💨 Steam

craft> /target Steam
  Target set: Steam — you'll be asked whether to continue the batch when this is crafted.

craft> /help
```

Sticky chrome under the log always shows the pair-API **rate** bar (next-slot wait + remaining budget). While a job runs it also shows **running** command + progress, or **◆ confirm** with the reason (pair count, target hit). `y` / `n` is only on `confirm [y/n]>`.

### Non-interactive mode

Most REPL commands are available as subcommands (shorthand operators like `+`, `++`, `+|`, and `*` are REPL-only):

```bash
infinite-craft combine "Water" "Fire"
infinite-craft search "steam"
infinite-craft list
infinite-craft recipe "Steam"
infinite-craft import "Steam"
infinite-craft export
infinite-craft fill
infinite-craft unfilled
infinite-craft prune
infinite-craft exhaust "Water"
infinite-craft crawl "Water" "Fire"
infinite-craft permute "w*"
infinite-craft with "Water" "fire*"
infinite-craft cross "fire*" "water*"
infinite-craft --version
```

## Commands

### Combine & crawl

| Shorthand | Slash command | Description |
|-----------|---------------|-------------|
| `<element> + <element>` | `/combine <element> <element>` | Combine two elements |
| `<element> ++ <element>` | `/crawl <element> <element>` | Combine & crawl until no new discoveries |

### Bulk combine

| Shorthand | Slash command | Description |
|-----------|---------------|-------------|
| `<element>` then `+|` then `<query>` (adjacent operator) | `/with <element> <query>` | Combine element with all matching discoveries |
| `<query> * <query>` | `/cross <query> <query>` | Cross-combine matches from both queries |
| | `/permute <query>` | Combine all matching elements with each other |
| | `/permutate <query>` | Permute repeatedly until no new discoveries |
| | `/exhaust <query>` | Each match combined with all discoveries |

### Query syntax

Used by `/search`, `/with`, `/permute`, `/permutate`, `/cross`, `/exhaust`, and the `+|` / `*` shorthands:

| Syntax | Meaning |
|--------|---------|
| `substring` | Case-insensitive substring (default) |
| `*` `?` `[]` | fnmatch wildcards (e.g. `fire*`, `mu?`) |
| `/pattern/` | Regex, case-insensitive (`/pattern/`; alternation `\|` not supported) |
| `!<query>` | Exclude matches (e.g. `!fire*` = everything except `fire*`) |
| `!` | All elements (exclude nothing) |
| `^<query>` | First discoveries only (e.g. `^fire*` = new `fire*` matches) |
| `^` | All first discoveries |

### Other commands

| Command | Description |
|---------|-------------|
| `/search <query>` | Search discoveries |
| `/recipe <element>` | Show shortest recipe from base elements |
| `/list` | List all discovered elements |
| `/import <element\|file.ic>` | Import from Infinibrowser or `.ic` save file |
| `/fill` | Fetch missing recipes from Infinibrowser |
| `/unfilled` | List elements without recipes |
| `/prune` | Remove orphan elements Infinibrowser can't fill |
| `/export [path]` | Export discoveries as `.ic` save file |
| `/history` | Show combinations tried this session |
| `/target <element>` | Watch for a result; ask y/n to continue the batch on hit |
| `/target` | Show current target |
| `/target clear` | Clear target |
| `/queue` | Show running and pending commands (status also appears in chrome) |
| `/help` | Show help |
| `/quit` | Exit |

### Queues and confirm (Python REPL + trainer)

Pair-API work (combine, crawl, permute, exhaust, …) and Infinibrowser work (`/fill`, `/prune`, `/import`) use **independent queues** so one lane can run while the other is busy. Local commands (`/help`, `/search`, `/list`, `/recipe`, `/history`, `/clear`, `/unfilled`, `/queue`, `/target`) run immediately — their output may interleave with a running job.

Large batches and `/target` hits pause on `confirm [y/n]>`: **y** continues, **n** / Esc / Stop cancels remaining work. The job row states the reason (`331 pairs`, `target hit`); keybindings are not repeated in the log.

| Key | Action |
|-----|--------|
| Esc | Skip current command, continue to next in that lane (TTY / Stop in the trainer) |
| Ctrl+C | While running: stop and discard remaining queue; at confirm: decline only |

Deferred commands print `Queued:` when that lane is already running.

## Data storage

Discoveries and recipes are stored in `~/.infinite-craft-cli/`:

- `discoveries.json` -- all discovered elements
- `recipes.json` -- known element combinations
- `export.ic` -- default export location

## Browser extension / bookmarklet

The [browser trainers](https://hacker6284.github.io/infinite-craft-cli/) share the same command syntax and query matching as the Python CLI (wildcards, `/regex/`, `!` exclude, `^` first discoveries, `/combine`, `/with`, `/cross`, `/target`, and operator shorthands `+`, `++`, `+|`, `*`). Browser-only additions: `/clear` to clear the output panel, and IndexedDB storage instead of `~/.infinite-craft-cli/`. Rate, job, and queue status live in the sticky panel above the input (visual-only; no `/queue` command). Local commands such as `/search` may interleave output with a running queued command.

**Long runs in a background tab:** the trainer holds a [Web Lock](https://developer.mozilla.org/en-US/docs/Web/API/Web_Locks_API) while a run is active, which keeps Chrome's Memory Saver / Energy Saver from freezing the tab under current heuristics. If a backgrounded run still pauses (the exemption is a heuristic, not a guarantee), add `neal.fun` to `chrome://settings/performance` → "Always keep these sites active", or keep the tab in a partially visible window. Truly long unattended jobs are what the Python CLI is for.

## Development

After editing `bookmarklet/trainer.src.mjs`, rebuild the bundles:

```bash
bazel build //bookmarklet:site
```

Build the Python wheel:

```bash
bazel build //release:wheel.dist
```

Run all tests:
```bash
bazel test //...
```

This includes `//sudo:craft_lockstep_test`, which runs the kernel's own test
suite against both the generated Python and JavaScript and diffs the two.
`//tests/...` alone skips it.

Run integration tests (hits the real API):
```bash
bazel test //tests:test_integration --test_env=INTEGRATION_TESTS=1
```

## License

MIT
