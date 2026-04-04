# infinite-craft-cli

[![PyPI](https://img.shields.io/pypi/v/infinite-craft-cli)](https://pypi.org/project/infinite-craft-cli/)
[![Downloads](https://img.shields.io/pypi/dm/infinite-craft-cli)](https://pypi.org/project/infinite-craft-cli/)
[![License](https://img.shields.io/badge/License-MIT-red?labelColor=black)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10+-blue)](https://python.org)
[![Tests](https://github.com/hacker6284/infinite-craft-cli/actions/workflows/test.yml/badge.svg)](https://github.com/hacker6284/infinite-craft-cli/actions/workflows/test.yml)
[![Publish](https://github.com/hacker6284/infinite-craft-cli/actions/workflows/publish.yml/badge.svg)](https://github.com/hacker6284/infinite-craft-cli/actions/workflows/publish.yml)

Interactive CLI for [Infinite Craft](https://neal.fun/infinite-craft/) — combine elements from the terminal.

Originally built on [infinite-craft](https://github.com/sqdnoises/infinite-craft) by [@sqdnoises](https://github.com/sqdnoises). As of v1.0, uses [curl_cffi](https://github.com/lexiforest/curl_cffi) directly.

## Installation

```bash
pip install infinite-craft-cli
```

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

All commands are available as subcommands:

```bash
infinite-craft combine "Water" "Fire"
infinite-craft search "steam"
infinite-craft list
infinite-craft recipe "Steam"
infinite-craft import "Steam"
infinite-craft export
infinite-craft fill
infinite-craft unfilled
infinite-craft exhaust "Water"
infinite-craft crawl "Water" "Fire"
infinite-craft permute "w*"
infinite-craft cross "fire*" "water*"
infinite-craft --version
```

## Commands

| Command | Description |
|---------|-------------|
| `<element> + <element>` | Combine two elements |
| `<element> ++ <element>` | Combine & crawl: iterate until no new discoveries |
| `<element> + \| <query>` | Combine element with all matching discoveries |
| `<query> * <query>` | Cross-combine all matches from both queries |
| `/search <query>` | Search discoveries (supports `*` `?` wildcards, `^` for first discoveries) |
| `/recipe <element>` | Show shortest recipe from base elements |
| `/list` | List all discovered elements |
| `/exhaust <element>` | Combine element with all discoveries |
| `/crawl <el> + <el>` | Same as `++` (alternate syntax) |
| `/permute <query>` | Combine all matching elements with each other |
| `/import <element\|file.ic>` | Import from Infinibrowser or `.ic` save file |
| `/fill` | Fetch missing recipes from Infinibrowser |
| `/unfilled` | List elements without recipes |
| `/export [path]` | Export discoveries as `.ic` save file |
| `/history` | Show combinations tried this session |
| `/help` | Show help |
| `/quit` | Exit |

## Data storage

Discoveries and recipes are stored in `~/.infinite-craft-cli/`:

- `discoveries.json` -- all discovered elements
- `recipes.json` -- known element combinations
- `export.ic` -- default export location

## Development

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
