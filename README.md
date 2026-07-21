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
craft> Water + Fire
  💨 Water + 🔥 Fire = 💨 Steam

craft> /search steam
  💨 Steam

craft> /help
```

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
| `/queue` | Show running and pending commands (status also appears above `craft>`) |
| `/help` | Show help |
| `/quit` | Exit |

### Background queue (Python REPL)

Long-running API commands queue FIFO. Local commands (`/help`, `/search`, `/list`, `/recipe`, `/history`, `/clear`, `/unfilled`, `/queue`) run immediately — output from a local command may interleave with a running queued command (e.g. `/search` during `/crawl`).

| Key | Action |
|-----|--------|
| Esc | Skip current command, continue to next in queue (TTY only) |
| Ctrl+C | While running: stop and discard remaining queue; at bulk confirm `[y/N]`: decline only |

Deferred commands print `Queued:` when another command is already running.

## Data storage

Discoveries and recipes are stored in `~/.infinite-craft-cli/`:

- `discoveries.json` -- all discovered elements
- `recipes.json` -- known element combinations
- `export.ic` -- default export location

## Browser extension / bookmarklet

The [browser trainers](https://hacker6284.github.io/infinite-craft-cli/) share the same command syntax and query matching as the Python CLI (wildcards, `/regex/`, `!` exclude, `^` first discoveries, `/combine`, `/with`, `/cross`, and operator shorthands `+`, `++`, `+|`, `*`). Browser-only additions: `/clear` to clear the output panel, and IndexedDB storage instead of `~/.infinite-craft-cli/`. Queue status is shown in the panel above the input (visual-only; no `/queue` command). Local commands such as `/search` may interleave output with a running queued command.

## Development

After editing `bookmarklet/trainer.js`, regenerate the minified artifact:

```bash
./bookmarklet/minify.sh
```

Run unit tests:
```bash
bazel test //tests/...
```

Run integration tests (hits the real API):
```bash
bazel test //tests:test_integration --test_env=INTEGRATION_TESTS=1
```

## License

MIT
