# Chrome pair lab

Headless-Chromium regression of the live trainer vs the game combine.

The game always `GET`s `/api/infinite-craft/pair`. The trainer goes
local `pairCache` → hive lookup → that same pair URL. This lab serves a
stand-in page with the real IndexedDB schema, injects the built
`trainer.js`, and mocks both the hive and the pair API.

## Seam

Observable results on the page, after one user action each:

- Game: click **Combine in game** → `#game-result` JSON (`result`, `emoji`, `isNew`).
- Trainer: type `Brokeback Fri-fries Island + Cowboy` at `craft>` → the
  `#ict-output` line `A + B = …`.

The pair names and the product **Brokeback Mountain** are the live
mismatch from 2026-10-11. Hive and neal answers are fixtures the test
sets; the browser, trainer bundle, IndexedDB, and `fetch` are real.

## Run

```
bazel test //tests/chrome:chrome_pair_test
```

Needs Playwright Chromium on the host (`python3 -m playwright install chromium`).
The target is `local` (no sandbox) so Chromium can launch, same compromise
as `//tests/parity:parity_test` needing system `node`.

Valid Nothing is a first-write hive hit, same as a product. An invalid
pair body (`{}` / `result: null` / `result: ""`) must error and must not
be contributed as Nothing. A token-backed pair (product or Nothing) is
contributed with a 5th `true` so the relay can refuse unproven Nothings.
