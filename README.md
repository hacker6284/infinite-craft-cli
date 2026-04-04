# infinite-craft-cli

Interactive CLI for [Infinite Craft](https://neal.fun/infinite-craft/) — combine elements from the terminal.

Built on top of [infinite-craft](https://github.com/sqdnoises/infinite-craft) by [@sqdnoises](https://github.com/sqdnoises).

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

```bash
infinite-craft combine "Water" "Fire"
infinite-craft search "steam"
infinite-craft list
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

## License

MIT
