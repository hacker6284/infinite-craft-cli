# Design: Consolidate infinite-craft-cli onto a single Bazel build path

- **Date:** 2026-08-04
- **Status:** Approved (design), pending implementation plan
- **Repo:** infinite-craft-cli
- **Related:** rules_sudo v0.3.0 migration (MODULE.bazel now consumes the published
  matched-pair release; see the `bazel-rules-sudo-1.0-migration` branch)

## Problem

The repo pins the `sudoc` toolchain **twice** and builds its release artifacts
through **two independent build systems**:

1. **Bazel path** — `MODULE.bazel` → `rules_sudo` (now v0.3.0). Drives
   `bazel test //...`: the `//sudo:craft` codegen, the `_sudo` py/js tree
   artifacts, the host tests, and `//sudo:craft_lockstep_test`.
2. **Standalone script path** — `scripts/sudoc-version.txt` (still pinned to
   **v0.2.0**) → `scripts/sudoc-bin.sh` → `scripts/generate.sh`. Produces the
   source-tree `_sudo/` adapters that non-Bazel consumers need:
   - `publish.yml` — builds the **PyPI wheel** (hatchling + hatch-vcs), which
     force-includes the gitignored `_sudo/`.
   - `pages.yml` — regenerates `_sudo/` then `bookmarklet/minify.sh`
     (`npx esbuild` + `npx terser`) bundles `trainer.js` / `trainer.min.js` for
     the **GitHub Pages** deploy the browser extension fetches.
   - `sudo.yml` — a standalone kernel-adapter regen + node smoke + bundle.
   - `release-dry-run.yml`, `tests/parity/run_parity.sh` (fallback regen).

The two sudoc pins have already drifted (Bazel v0.3.0 vs script v0.2.0). Anyone
running `scripts/generate.sh` would regenerate **v0.2.0** codegen, contradicting
the Bazel path. The duplication is the root cause.

## Goals

- **One toolchain**: `rules_sudo` v0.3.0 is the only `sudoc` pin.
- **One build system**: every release artifact (wheel, bookmarklet bundle) is a
  Bazel target; CI only runs `bazel`.
- Delete the standalone script path entirely.

## Non-goals

- No change to `sudo/craft.sudo` behavior or the CLI/bookmarklet UX.
- No change to the extension's thin-loader model (it still fetches
  `trainer.min.js` from GitHub Pages).
- Byte-for-byte reproduction of the current minified output is **not** required
  (we are free to change the minifier).

## Target architecture

```
sudo/craft.sudo ──(rules_sudo v0.3.0, sudoc)──► //sudo:craft
                                                   │
              ┌────────────────────────────────────┼─────────────────────────┐
              ▼                                     ▼                          ▼
  //src/infinite_craft_cli:_sudo        //bookmarklet:_sudo         //sudo:craft_lockstep_test
     (py tree artifact,                    (js tree artifact)
      incl. sys.path __init__.py            │
      emitted by rules_sudo)               ▼
              │                    //bookmarklet:trainer_js  ──►  //bookmarklet:trainer_min_js
              ▼                       (esbuild bundle+banner)        (esbuild bundle + --minify)
  //release:wheel (py_wheel)                │                          │
     version ← tools/workspace_status.sh    └───────────┬──────────────┘
                                                        ▼
                                            //bookmarklet:site
                                     (index.html, trainer.user.js + built bundle)
```

`_sudo/` stays **gitignored** (pure build input). `trainer.js` / `trainer.min.js`
become **build-only** outputs — the committed copies are deleted.

## Detailed design

### A. PyPI wheel — `//release:wheel` (`py_wheel`, rules_python)

`rules_python` is already a dep (1.7.0); `py_wheel` lives in
`@rules_python//python:packaging.bzl`.

- **Contents**: `deps = ["//src/infinite_craft_cli:infinite_craft_cli"]`,
  `strip_path_prefixes = ["src"]`. The py_library already depends on `:_sudo`
  (the tree artifact), whose `__init__.py` sys.path shim is emitted by
  rules_sudo v0.3.0 — so the wheel gets the shim for free (no hand-maintained
  copy, unlike `generate.sh` today).
- **Metadata parity** with the current hatchling wheel:
  - `distribution = "infinite-craft-cli"`
  - `requires = ["curl-cffi>=0.15"]`
  - `python_requires = ">=3.10"`
  - `classifiers = [...]` (copy from `pyproject.toml`)
  - `summary`, `description_file = "//:README.md"`, `homepage`, `project_urls`
  - `license = "MIT"`, LICENSE file included via `extra_distinfo_files`
  - `console_scripts`: `infinite-craft = infinite_craft_cli.cli:main`
- **Version**: `tools/workspace_status.sh` (wired via
  `build --workspace_status_command` in `.bazelrc`) emits
  `STABLE_VERSION <git describe --tags --dirty>`, normalized to a PEP 440 string
  (clean `X.Y.Z` on a tag, `X.Y.Z.devN+g<sha>` off-tag) to mirror hatch-vcs.
  `py_wheel(version = "{STABLE_VERSION}")`.
- **CI publish**: `publish.yml` runs
  `bazel build //release:wheel` then `pypa/gh-action-pypi-publish` on the wheel
  in `bazel-bin/release/`. A post-build **install smoke** step (`pip install` the
  wheel in a throwaway venv, `import infinite_craft_cli._sudo.craft`) preserves
  the existing "wheel actually contains the kernel" guarantee.

### B. Bookmarklet bundle — `aspect_rules_esbuild`

New deps in `MODULE.bazel`: `aspect_rules_esbuild` (brings a hermetic esbuild
toolchain, and transitively `aspect_rules_js` + hermetic Node for the smoke
test). **No pnpm lockfile needed** — esbuild ships via its own toolchain and the
smoke test has no npm deps. **Terser is dropped**; esbuild's own `--minify`
produces the minified file.

`//bookmarklet/BUILD.bazel` gains:

- `esbuild(name = "trainer_js", entry_point = "trainer.src.mjs", srcs = [":_sudo"], format = "iife", output = "trainer.js")` with the existing banner
  (`Built artifact — do not edit …`) applied via the esbuild config.
- `esbuild(name = "trainer_min_js", … , minify = True, output = "trainer.min.js")`.
- `//bookmarklet:site` — a directory/`pkg_files` target combining committed
  static assets (`index.html`, `trainer.user.js`) with `:trainer_js` +
  `:trainer_min_js`. Pages uploads this.
- `//bookmarklet:kernel_smoke_test` — a `js_test` running
  `test_kernel_smoke.mjs` with `data = [":_sudo"]`.

### C. Test rewiring (build-only artifacts)

Today `help_utils.py`, `test_formatting.py`, and `test_trainer_parity.py` read
`ROOT / "bookmarklet" / "trainer.{js,min.js}"` where
`ROOT = Path(__file__).resolve().parent.parent`. Under Bazel, `.resolve()`
follows the runfiles symlink back into the **source checkout**, so they read the
committed files — non-hermetically. Deleting the committed files breaks this.

Fix: these tests consume the **Bazel-built** artifact via runfiles.

- Add `data = ["//bookmarklet:trainer_js"]` (and `:trainer_min_js` where the
  min file is read) to the relevant `py_test`s / the `help_utils` lib.
- Replace the `.resolve()`-escape with a runfiles locator: use
  `rules_python`'s `python.runfiles` (`Runfiles.Create().Rlocation(...)`),
  wrapped in a small helper. Provide a filesystem fallback (env var or repo-root
  probe) so a plain `pytest` dev run still works when the built file is present.
- `test_extension_loader.py`: drop the `npx terser` drift subprocess entirely
  (no committed min.js to compare against); keep its loader-structure assertions
  (`index.html` references `trainer.min.js`, the extension does not bundle
  `trainer.js`, etc.), reading built files via runfiles where needed.
- `test_formatting.py` sentinel check: the needles are **string literals**
  (`"Unknown command:"`, `"/combine <element> <element>"`, …) which survive
  esbuild `--minify` (strings are preserved; only identifiers are renamed). Point
  it at `//bookmarklet:trainer_min_js`.

### D. CI workflows

- `publish.yml`: replace `generate.sh` + `python -m build` with
  `bazel build //release:wheel` (+ install smoke) → pypi-publish. Keep the
  changelog/GitHub-release job unchanged.
- `pages.yml`: replace `generate.sh` + `minify.sh` with
  `bazel build //bookmarklet:site` → `upload-pages-artifact` on the built site
  dir.
- `test.yml`: still `bazel test //...` (now also runs the hermetic smoke +
  parity tests); add `bazel build //release:wheel //bookmarklet:site` to catch
  build breaks. Drop `actions/setup-node` (rules_esbuild provides hermetic Node).
- `release-dry-run.yml`: repoint its `generate.sh` steps to the Bazel builds.
- **Delete** `.github/workflows/sudo.yml` (its roles — adapter regen, node
  smoke, bundle — are now `bazel test`/`bazel build`).

### E. Deletions & doc updates

- Delete: `scripts/generate.sh`, `scripts/sudoc-bin.sh`,
  `scripts/sudoc-version.txt`, `bookmarklet/minify.sh`,
  `.github/workflows/sudo.yml`. Remove the now-empty `scripts/` dir if nothing
  remains.
- `git rm bookmarklet/trainer.js bookmarklet/trainer.min.js`.
- `.gitignore`: drop the `.cache/` (sudoc download cache) line; the
  `_sudo/` ignores stay.
- `tests/parity/run_parity.sh`: remove the `generate.sh` fallback (adapters now
  come from Bazel) or delete the script if Bazel covers parity.
- `PRIVACY.md`: reword the trust model. Current text says to "compare served
  `trainer.min.js` against the committed artifact." There is no committed
  artifact anymore; the new story is "CI rebuilds `trainer.min.js` from source
  (`sudo/craft.sudo` transpiled via the pinned rules_sudo release +
  `trainer.src.mjs`) on every deploy." Update `CHANGELOG.md` accordingly.

## Risks & verification

1. **`py_wheel` + tree artifact (top risk)** — confirm `py_wheel` packages the
   `_sudo` directory (a tree artifact reached via `PyInfo`). Verify with
   `unzip -l` on the built wheel vs the current hatchling wheel (same file set
   under `infinite_craft_cli/_sudo/`). Fallback: include `_sudo` explicitly via
   `pkg_files`/`filegroup` in the wheel's `data`.
2. **Version stamping** — verify a clean `X.Y.Z` on a tag and a
   `…devN+g<sha>` off-tag; ensure the wheel filename + `METADATA` version match
   what PyPI expects and that re-publish of an existing version is not attempted.
3. **Minify sentinels** — build `//bookmarklet:trainer_min_js` and grep for the
   five `test_formatting` needles before rewiring the test.
4. **Runfiles locator** — the helper must resolve under `bazel test` (sandbox)
   and degrade gracefully under raw `pytest`.
5. **Hermetic Node in CI** — confirm the Pages and test workflows build without
   `actions/setup-node`.

## Rollout order (for the plan)

1. Add Bazel targets (`//release:wheel`, esbuild bundle, `//bookmarklet:site`,
   `js_test`, `workspace_status.sh`) — additive, nothing deleted yet.
2. Verify wheel contents + bundle output locally (`bazel build`, `unzip -l`,
   sentinel grep).
3. Rewire tests to runfiles; `bazel test //...` green.
4. Repoint CI workflows; delete `sudo.yml`.
5. Delete the standalone scripts + committed artifacts; update
   `PRIVACY.md` / `CHANGELOG.md` / `.gitignore` / `run_parity.sh`.
6. Full `bazel test //...` + `bazel build //release:wheel //bookmarklet:site`
   green; dry-run the publish + pages flows.
