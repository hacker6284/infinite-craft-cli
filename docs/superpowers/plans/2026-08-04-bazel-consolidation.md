# Bazel Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Bazel (rules_sudo v0.3.0) the single toolchain and single build system for infinite-craft-cli, producing the PyPI wheel and the build-only bookmarklet bundle, and delete the standalone `sudoc` script path.

**Architecture:** Add Bazel targets for the wheel (`py_wheel`) and the bookmarklet bundle (`aspect_rules_esbuild`, build-only `trainer.js`/`trainer.min.js`), rewire host tests that read the bundle to consume the Bazel-built artifact via runfiles, repoint CI to `bazel`, then delete `scripts/generate.sh` + `scripts/sudoc-bin.sh` + `scripts/sudoc-version.txt` + `bookmarklet/minify.sh` + `.github/workflows/sudo.yml` and the committed bundle files.

**Tech Stack:** Bazel (bzlmod), rules_sudo v0.3.0, rules_python `py_wheel`, aspect_rules_esbuild + aspect_rules_js (hermetic esbuild/Node), GitHub Actions.

## Global Constraints

- **sudoc toolchain:** rules_sudo `1.0.0` consumed from release **v0.3.0** (already wired in `MODULE.bazel`). No second sudoc pin may be introduced.
- **Python floor:** `requires-python >= 3.10`; wheel built for `py3`.
- **Runtime dep:** `curl-cffi>=0.15` (metadata `requires`, not vendored).
- **Console script:** `infinite-craft = infinite_craft_cli.cli:main`.
- **Wheel package name:** `infinite-craft-cli`; import package `infinite_craft_cli`.
- **esbuild version:** `0.28.1` (matches the retired `minify.sh`).
- **Banner** on `trainer.js` (verbatim): `/* Built artifact — do not edit. Single source of truth: trainer.src.mjs\n * (UI/effects) + ../sudo/craft.sudo (kernel, transpiled via sudoc). */`
- **Build-only artifacts:** `bookmarklet/trainer.js` and `bookmarklet/trainer.min.js` must NOT be committed after this plan; they exist only in `bazel-bin`.
- **Commit style:** end messages with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Work stays on branch `bazel-rules-sudo-1.0-migration`.
- **Verification-before-completion:** every task ends with the exact `bazel`/CLI command run and its observed result before commit.

---

## File Structure

- `tools/workspace_status.sh` (create) — emits `STABLE_VERSION` for wheel stamping.
- `.bazelrc` (modify) — wire `--workspace_status_command`.
- `release/BUILD.bazel` (create) — `//release:wheel` (`py_wheel`).
- `MODULE.bazel` (modify) — add `aspect_rules_esbuild` + esbuild toolchain.
- `bookmarklet/esbuild.config.mjs` (create) — esbuild banner config.
- `bookmarklet/BUILD.bazel` (modify) — `trainer_js`, `trainer_min_js`, `site`, `kernel_smoke_test`.
- `tests/artifact_paths.py` (create) — runfiles locator for the built bundle.
- `tests/help_utils.py`, `tests/test_formatting.py`, `tests/test_trainer_parity.py`, `tests/test_extension_loader.py` (modify) — read built artifact via the locator.
- `tests/BUILD.bazel` (modify) — data deps on the bundle for the rewired tests.
- `.github/workflows/{publish,pages,test,release-dry-run}.yml` (modify), `.github/workflows/sudo.yml` (delete).
- `scripts/generate.sh`, `scripts/sudoc-bin.sh`, `scripts/sudoc-version.txt`, `bookmarklet/minify.sh` (delete).
- `bookmarklet/trainer.js`, `bookmarklet/trainer.min.js` (delete).
- `.gitignore`, `PRIVACY.md`, `CHANGELOG.md`, `tests/parity/run_parity.sh` (modify).

---

## Task 1: Version stamping for the wheel

**Files:**
- Create: `tools/workspace_status.sh`
- Modify: `.bazelrc`

**Interfaces:**
- Produces: a stable stamp key `STABLE_VERSION` (PEP 440 string) consumed by `//release:wheel` in Task 2 as `version = "{STABLE_VERSION}"`.

- [ ] **Step 1: Create `tools/workspace_status.sh`**

```bash
#!/usr/bin/env bash
# Emits Bazel stable stamp vars. STABLE_VERSION mirrors hatch-vcs:
#   on an exact tag  vX.Y.Z            -> X.Y.Z
#   N commits after  vX.Y.Z-N-g<sha>   -> X.Y.Z.devN+g<sha>
#   dirty tree                          -> ...+dirty appended
# Shallow/tagless checkouts (e.g. CI PRs with fetch-depth 1) -> 0.0.0, which the
# publish workflow refuses to upload (guard in Task 6).
set -euo pipefail
raw="$(git describe --tags --long --dirty 2>/dev/null || true)"
if [ -z "$raw" ]; then
  echo "STABLE_VERSION 0.0.0"
  exit 0
fi
raw="${raw#v}"                                   # strip leading v
# --long always yields "X.Y.Z-N-gSHA[-dirty]".
base="$(printf '%s' "$raw" | sed -E 's/-[0-9]+-g[0-9a-f]+(-dirty)?$//')"
suffix="$(printf '%s' "$raw" | sed -nE 's/^.*-([0-9]+)-g([0-9a-f]+)(-dirty)?$/\1 \2 \3/p')"
if [ -z "$suffix" ] || [ -z "$base" ]; then
  echo "STABLE_VERSION 0.0.0"                    # unparseable describe output
  exit 0
fi
n="$(printf '%s' "$suffix" | awk '{print $1}')"
sha="$(printf '%s' "$suffix" | awk '{print $2}')"
dirty="$(printf '%s' "$suffix" | awk '{print $3}')"
if [ "$n" = "0" ] && [ -z "$dirty" ]; then
  version="$base"
else
  version="${base}.dev${n}+g${sha}"
  [ -n "$dirty" ] && version="${version}.dirty"
fi
echo "STABLE_VERSION ${version}"
```

- [ ] **Step 2: `chmod +x tools/workspace_status.sh`**

Run: `chmod +x tools/workspace_status.sh`

- [ ] **Step 3: Wire it in `.bazelrc`**

Append:
```
# Stamp STABLE_VERSION (tools/workspace_status.sh) for //release:wheel.
build --workspace_status_command=tools/workspace_status.sh
```

- [ ] **Step 4: Verify the stamp emits a sane version**

Run: `bash tools/workspace_status.sh`
Expected: a single line like `STABLE_VERSION 1.5.0` (on a tag) or `STABLE_VERSION 1.5.0.dev3+g<sha>` (off-tag). Non-empty version, no `v` prefix.

- [ ] **Step 5: Commit**

```bash
git add tools/workspace_status.sh .bazelrc
git commit -m "build: STABLE_VERSION stamp for wheel packaging

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `//release:wheel` (py_wheel)

**Files:**
- Create: `release/BUILD.bazel`
- Modify: `BUILD.bazel` (repo root — add `exports_files` for README/LICENSE), `src/infinite_craft_cli/BUILD.bazel` (add a `_sudo_files` filegroup so the tree artifact reaches the wheel as a **direct** dep file).
- Reference (do not modify): `pyproject.toml` (source of truth for metadata to copy).

**Interfaces:**
- Consumes: `{STABLE_VERSION}` from Task 1; `//src/infinite_craft_cli:infinite_craft_cli` (the `.py` sources) and `//src/infinite_craft_cli:_sudo_files` (the kernel tree artifact).
- Produces: `//release:wheel` and its implicit `//release:wheel.dist` copy target → `bazel-bin/release/wheel_dist/infinite_craft_cli-<version>-py3-none-any.whl`, consumed by `publish.yml` in Task 6.

> **Why `_sudo_files` is required (Fable review):** `py_wheel` packages
> `depset(direct = ctx.files.deps)` — direct dep files ONLY, no PyInfo/runfiles
> traversal. The py_library's `.py` srcs are its direct files (included), but
> `:_sudo` is a *transitive* dep of that library, so it is invisible to the
> wheel unless named directly. A `filegroup` re-exposes the tree artifact as a
> direct file; `tools/wheelmaker.py` recurses into directory inputs, so the
> whole `_sudo/` tree lands in the wheel. `strip_path_prefixes=["src"]` operates
> on `short_path` (`src/infinite_craft_cli/...`), so files land under
> `infinite_craft_cli/...`; the py_library's `imports=[".."]` is irrelevant to
> packaging.

- [ ] **Step 1a: Add `exports_files` to the repo-root `BUILD.bazel`**

The wheel references `//:README.md` and `//:LICENSE`; the root package must
export them. Append to `BUILD.bazel`:
```starlark
exports_files(["README.md", "LICENSE"])
```

- [ ] **Step 1b: Add the `_sudo_files` filegroup**

Append to `src/infinite_craft_cli/BUILD.bazel`:
```starlark
# Re-expose the _sudo tree artifact as a direct file so py_wheel (which packages
# only ctx.files.deps, no PyInfo traversal) includes the kernel.
filegroup(
    name = "_sudo_files",
    srcs = [":_sudo"],
    visibility = ["//visibility:public"],
)
```

- [ ] **Step 1c: Create `release/BUILD.bazel`**

```starlark
load("@rules_python//python:packaging.bzl", "py_wheel")

# Single-source wheel: packages the pure-Python CLI plus the rules_sudo-emitted
# _sudo/ kernel (a tree artifact reached transitively through the py_library's
# PyInfo). strip_path_prefixes drops the "src/" layout root so files land under
# infinite_craft_cli/… in the wheel.
py_wheel(
    name = "wheel",
    distribution = "infinite-craft-cli",
    version = "{STABLE_VERSION}",
    python_tag = "py3",
    abi = "none",
    platform = "any",
    python_requires = ">=3.10",
    summary = "Interactive CLI for Infinite Craft — combine elements from the terminal",
    description_file = "//:README.md",
    description_content_type = "text/markdown",
    homepage = "https://github.com/hacker6284/infinite-craft-cli",
    project_urls = {
        "Homepage": "https://github.com/hacker6284/infinite-craft-cli",
        "Repository": "https://github.com/hacker6284/infinite-craft-cli",
    },
    license = "MIT",
    classifiers = [
        "Development Status :: 5 - Production/Stable",
        "Environment :: Console",
        "Topic :: Games/Entertainment",
        "Programming Language :: Python :: 3",
    ],
    requires = ["curl-cffi>=0.15"],
    entry_points = {"console_scripts": ["infinite-craft = infinite_craft_cli.cli:main"]},
    extra_distinfo_files = {"//:LICENSE": "LICENSE"},
    strip_path_prefixes = ["src"],
    stamp = 1,  # forces stamping; the CLI --stamp flag is then redundant
    deps = [
        "//src/infinite_craft_cli:infinite_craft_cli",  # the .py sources (direct files)
        "//src/infinite_craft_cli:_sudo_files",          # the kernel tree artifact
    ],
    visibility = ["//visibility:public"],
)
```

Note for the implementer: `py_wheel` attribute names can vary slightly by
rules_python version. Confirm each attribute exists in
`@rules_python//python:packaging.bzl` for the pinned `rules_python` 1.7.0
(`bazel query --output=build @rules_python//python:packaging.bzl` or the
rules_python docs). If `description_content_type`/`project_urls` are unsupported
in 1.7.0, drop them — they are cosmetic metadata, not correctness.

- [ ] **Step 2: Build the wheel (use the `.dist` copy target — stable filename)**

The primary `//release:wheel` output embeds the stamped version in its filename
via a placeholder that is only resolved by the implicit `.dist` copy target. Build
the `.dist` target so downstream steps read a real filename:

Run: `bazel build //release:wheel.dist`
Expected: SUCCESS; a real `.whl` (version in the name, no `{...}` placeholder)
under `bazel-bin/release/wheel_dist/`.

- [ ] **Step 3: Verify wheel contents match the current package (top risk)**

Run:
```bash
unzip -l "$(echo bazel-bin/release/wheel_dist/infinite_craft_cli-*.whl)" | sort
```
Expected: the archive contains `infinite_craft_cli/cli.py` (and siblings), the
full `infinite_craft_cli/_sudo/` tree (`craft.py`, `_craft_impl.py`,
`_regex_impl.py`, `_strings_impl.py`, `_sudo_rt.py`, `regex.py`, `__init__.py`),
a `console_scripts` `infinite-craft` entry in the `entry_points.txt`, and the
LICENSE in `*.dist-info/`. If `_sudo/` is missing, the `_sudo_files` filegroup
(Step 1b) is not in `deps` — fix and rebuild.

- [ ] **Step 4: Install-smoke the wheel**

Run:
```bash
python3 -m venv /tmp/icw && /tmp/icw/bin/pip install -q "$(echo bazel-bin/release/wheel_dist/infinite_craft_cli-*.whl)" && /tmp/icw/bin/python -c "import infinite_craft_cli._sudo.craft; print('kernel import OK')"
```
Expected: prints `kernel import OK`.

- [ ] **Step 5: Commit**

```bash
git add BUILD.bazel release/BUILD.bazel src/infinite_craft_cli/BUILD.bazel
git commit -m "build: //release:wheel builds the PyPI wheel via py_wheel

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: esbuild bundle targets

**Files:**
- Modify: `MODULE.bazel`
- Create: `bookmarklet/esbuild.config.mjs`
- Modify: `bookmarklet/BUILD.bazel`

**Interfaces:**
- Consumes: `//bookmarklet:_sudo` (existing `sudo_js_library` tree artifact), `bookmarklet/trainer.src.mjs`.
- Produces: `//bookmarklet:trainer_js` → `trainer.js`, `//bookmarklet:trainer_min_js` → `trainer.min.js`. Consumed by Tasks 4, 5, 6.

- [ ] **Step 1: Add the esbuild ruleset to `MODULE.bazel`**

Append:
```starlark
# Hermetic esbuild (bundles the bookmarklet; replaces npx esbuild/terser).
bazel_dep(name = "aspect_rules_esbuild", version = "0.22.1")
# Directly loaded by //bookmarklet (js_test, copy_to_directory). Under bzlmod a
# root module can only load repos it declares — transitive deps of
# aspect_rules_esbuild are NOT visible — so these are explicit even though
# rules_esbuild also pulls them in. (Fable review.)
bazel_dep(name = "aspect_rules_js", version = "2.1.0")
bazel_dep(name = "aspect_bazel_lib", version = "2.9.4")

esbuild = use_extension("@aspect_rules_esbuild//esbuild:extensions.bzl", "esbuild")
esbuild.toolchain(esbuild_version = "0.28.1")
use_repo(esbuild, "esbuild_toolchains")
register_toolchains("@esbuild_toolchains//:all")
```

Note for the implementer: pin all three rulesets to compatible versions on the
Bazel Central Registry (match `aspect_rules_js`/`aspect_bazel_lib` to whatever
the chosen `aspect_rules_esbuild` depends on — check its `MODULE.bazel` on BCR),
and confirm the extension/toolchain API in the rules_esbuild README for that
version (the `use_extension` path and `esbuild.toolchain` attr name have changed
across releases). If **esbuild 0.28.1 is not in that ruleset's known-versions
table**, use the ruleset's default esbuild version — byte-parity with the old
`minify.sh` is an explicit non-goal. Run `bazel mod deps` after editing to
confirm resolution.

- [ ] **Step 2: Create `bookmarklet/esbuild.config.mjs`**

```javascript
// esbuild config for the trainer bundle. Banner marks the output as generated.
// This is an .mjs file, so it MUST use ESM `export default`, not `module.exports`
// (a CommonJS `module.exports` in an .mjs throws at load). (Fable review.)
export default {
  banner: {
    js: "/* Built artifact — do not edit. Single source of truth: trainer.src.mjs\n * (UI/effects) + ../sudo/craft.sudo (kernel, transpiled via sudoc). */",
  },
};
```

- [ ] **Step 3: Add the bundle targets to `bookmarklet/BUILD.bazel`**

```starlark
load("@aspect_rules_esbuild//esbuild:defs.bzl", "esbuild")

# Bundled, unminified trainer (host tests + local dev read this).
esbuild(
    name = "trainer_js",
    entry_point = "trainer.src.mjs",
    srcs = ["trainer.src.mjs", ":_sudo"],
    config = "esbuild.config.mjs",
    format = "iife",
    output = "trainer.js",
    visibility = ["//visibility:public"],
)

# Minified trainer (served by GitHub Pages, fetched by the extension).
esbuild(
    name = "trainer_min_js",
    entry_point = "trainer.src.mjs",
    srcs = ["trainer.src.mjs", ":_sudo"],
    config = "esbuild.config.mjs",
    format = "iife",
    minify = True,
    output = "trainer.min.js",
    visibility = ["//visibility:public"],
)
```

- [ ] **Step 4: Build both bundles**

Run: `bazel build //bookmarklet:trainer_js //bookmarklet:trainer_min_js`
Expected: SUCCESS; `bazel-bin/bookmarklet/trainer.js` and `trainer.min.js` exist.
If esbuild cannot resolve `./_sudo/craft.mjs`, verify the `:_sudo` tree artifact
lands at `bookmarklet/_sudo/` relative to the entry (it is named `_sudo` in this
package); if not, add `deps = [":_sudo"]` alongside `srcs` or a `root_dirs` arg
per the rules_esbuild docs, and re-run.

- [ ] **Step 5: Verify banner + minify sentinels**

Run:
```bash
head -2 bazel-bin/bookmarklet/trainer.js
for n in "no space between + and |" "/combine <element> <element>" "Use <element> +| <query>" "Unknown command:" "Already queued."; do
  grep -qF "$n" bazel-bin/bookmarklet/trainer.min.js && echo "OK: $n" || echo "MISSING: $n"
done
```
Expected: line 1 of `trainer.js` is the banner; all five sentinels print `OK`
(they are string literals, preserved by minification).

- [ ] **Step 6: Commit**

```bash
git add MODULE.bazel MODULE.bazel.lock bookmarklet/esbuild.config.mjs bookmarklet/BUILD.bazel
git commit -m "build: hermetic esbuild bundle targets for the trainer

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Pages site target + node smoke test

**Files:**
- Modify: `bookmarklet/BUILD.bazel`

**Interfaces:**
- Consumes: `//bookmarklet:trainer_js`, `//bookmarklet:trainer_min_js`, `//bookmarklet:_sudo`, `bookmarklet/index.html`, `bookmarklet/trainer.user.js`, `bookmarklet/test_kernel_smoke.mjs`.
- Produces: `//bookmarklet:site` (deploy dir for Pages, Task 6), `//bookmarklet:kernel_smoke_test` (`js_test`).

- [ ] **Step 1: Add the site + smoke targets to `bookmarklet/BUILD.bazel`**

```starlark
load("@aspect_rules_js//js:defs.bzl", "js_test")
load("@aspect_bazel_lib//lib:copy_to_directory.bzl", "copy_to_directory")

# The exact directory GitHub Pages uploads: committed static assets + built bundle.
copy_to_directory(
    name = "site",
    srcs = [
        "index.html",
        "trainer.user.js",
        ":trainer_js",
        ":trainer_min_js",
    ],
    root_paths = ["bookmarklet"],
    visibility = ["//visibility:public"],
)

# Node smoke of ex-bug kernel behaviors (was sudo.yml's `node test_kernel_smoke.mjs`).
js_test(
    name = "kernel_smoke_test",
    entry_point = "test_kernel_smoke.mjs",
    data = [":_sudo"],
)
```

- [ ] **Step 2: Build the site dir**

Run: `bazel build //bookmarklet:site`
Expected: SUCCESS; `bazel-bin/bookmarklet/site/` contains `index.html`,
`trainer.user.js`, `trainer.js`, `trainer.min.js` at the top level.
If `copy_to_directory`'s `root_paths` does not flatten `bookmarklet/…` to the
directory root, adjust `root_paths`/`replace_prefixes` per aspect_bazel_lib docs
until the four files sit at the site root.

- [ ] **Step 3: Run the smoke test**

Run: `bazel test //bookmarklet:kernel_smoke_test --test_output=errors`
Expected: PASS. If `test_kernel_smoke.mjs` imports `./_sudo/craft.mjs` and the
path does not resolve, add the `_sudo` dir to `data` and confirm the runfiles
layout (`bookmarklet/_sudo/craft.mjs`) matches the import.

- [ ] **Step 4: Commit**

```bash
git add bookmarklet/BUILD.bazel
git commit -m "build: //bookmarklet:site deploy dir + hermetic kernel smoke js_test

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Rewire host tests to the built bundle

**Files:**
- Create: `tests/artifact_paths.py`
- Modify: `tests/help_utils.py`, `tests/test_formatting.py`, `tests/test_trainer_parity.py`, `tests/test_extension_loader.py`, `tests/BUILD.bazel`

**Interfaces:**
- Consumes: `//bookmarklet:trainer_js`, `//bookmarklet:trainer_min_js`.
- Produces: `tests/artifact_paths.py` exposing `trainer_js_path() -> Path` and `trainer_min_js_path() -> Path`, used by the rewired tests.

- [ ] **Step 1: Write the failing locator + a test for it**

Create `tests/artifact_paths.py`:
```python
"""Locate the Bazel-built trainer bundle from tests.

Under `bazel test` the files arrive as runfiles data deps; under a plain
`pytest` dev run they are read from `bazel-bin/` (build them first with
`bazel build //bookmarklet:trainer_js //bookmarklet:trainer_min_js`).
"""
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _runfiles_path(relative: str) -> Path | None:
    try:
        from python.runfiles import runfiles  # type: ignore
    except Exception:
        return None
    r = runfiles.Create()
    if r is None:
        return None
    found = r.Rlocation("_main/" + relative)
    return Path(found) if found and Path(found).exists() else None


def _bin_path(relative: str) -> Path:
    # relative is e.g. "bookmarklet/trainer.js" -> bazel-bin/bookmarklet/trainer.js
    return _REPO_ROOT / "bazel-bin" / relative


def trainer_js_path() -> Path:
    return _runfiles_path("bookmarklet/trainer.js") or _bin_path("bookmarklet/trainer.js")


def trainer_min_js_path() -> Path:
    return _runfiles_path("bookmarklet/trainer.min.js") or _bin_path("bookmarklet/trainer.min.js")
```

Add to `tests/` a focused test (append to `tests/test_formatting.py`'s module or a new `tests/test_artifact_paths.py`):
```python
def test_trainer_js_locator_resolves():
    from tests.artifact_paths import trainer_js_path
    assert trainer_js_path().exists()
```

- [ ] **Step 2: Update `tests/BUILD.bazel` so the locator + bundle are available**

Add an `artifact_paths` lib and give the bundle-reading tests the data deps.
Replace the `help_utils` lib and the `py_test` list with:
```starlark
py_library(
    name = "artifact_paths",
    srcs = ["artifact_paths.py"],
    imports = [".."],
    data = [
        "//bookmarklet:trainer_js",
        "//bookmarklet:trainer_min_js",
    ],
    deps = ["@rules_python//python/runfiles"],
)

py_library(
    name = "help_utils",
    srcs = ["help_utils.py"],
    imports = [".."],
    deps = [
        ":artifact_paths",
        "//src/infinite_craft_cli",
        "@pip//pytest",
    ],
)

[py_test(
    name = test_file.removesuffix(".py"),
    srcs = [test_file],
    main = test_file,
    imports = [".."],
    deps = [
        ":artifact_paths",
        ":conftest",
        ":help_utils",
        "//src/infinite_craft_cli",
        "@pip//pytest",
        "@pip//pytest_asyncio",
    ],
    data = [
        "//bookmarklet:trainer_js",
        "//bookmarklet:trainer_min_js",
    ],
) for test_file in glob(
    ["test_*.py"],
    exclude = ["test_integration.py"],
)]
```
(Leave the `test_integration` target and `conftest` lib unchanged.)

- [ ] **Step 3: Repoint `help_utils.py`**

Change `extract_js_help_plaintext` to default to the built artifact:
```python
from tests.artifact_paths import trainer_js_path

def extract_js_help_plaintext(trainer_path: Path | None = None) -> str:
    """Extract doHelp() template literals from the built trainer.js as plain text."""
    path = trainer_path or trainer_js_path()
    source = path.read_text(encoding="utf-8")
    ...  # rest unchanged
```
Delete the `ROOT / "bookmarklet" / "trainer.js"` fallback line.

- [ ] **Step 4: Repoint `test_formatting.py` and `test_trainer_parity.py`**

In `test_formatting.py`:
```python
from tests.artifact_paths import trainer_min_js_path
# ...
        min_js = trainer_min_js_path().read_text()
```
In `test_trainer_parity.py`:
```python
from tests.artifact_paths import trainer_js_path
# ...
        source = trainer_js_path().read_text(encoding="utf-8")
```
Remove the now-unused `ROOT` definitions if nothing else uses them.

- [ ] **Step 5: Rework `test_extension_loader.py`**

Delete the `npx terser` drift subprocess (the `subprocess.run(["npx", …, "terser@…", …])` block and its comparison to a committed `trainer.min.js`). Keep every
loader-structure assertion (`fetch('trainer.min.js'` in `index.html`,
`"trainer.js" not in loader`, extension does not bundle `trainer.js`, etc.). Where
it needs the built min file, use `trainer_min_js_path()`.

- [ ] **Step 6: Run the rewired tests under Bazel**

Run: `bazel test //tests:test_formatting //tests:test_trainer_parity //tests:test_extension_loader //tests:test_artifact_paths --test_output=errors`
Expected: all PASS, now reading the freshly built bundle (hermetic).

- [ ] **Step 7: Full suite**

Run: `bazel test //...  --test_output=errors`
Expected: all PASS (host tests, parity lockstep, kernel smoke).

- [ ] **Step 8: Commit**

```bash
git add tests/
git commit -m "test: read the Bazel-built trainer bundle via runfiles (hermetic)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Repoint CI workflows

**Files:**
- Modify: `.github/workflows/publish.yml`, `.github/workflows/pages.yml`, `.github/workflows/test.yml`, `.github/workflows/release-dry-run.yml`
- Delete: `.github/workflows/sudo.yml`

**Interfaces:**
- Consumes: `//release:wheel`, `//bookmarklet:site` from Tasks 2/4.

- [ ] **Step 1: `publish.yml` — build the wheel with Bazel**

Replace the `Generate kernel adapters` + `setup-python` + `Install build tools` +
`Build package` + `Wheel actually contains the kernel` steps of the `publish` job
with:
```yaml
      - uses: bazel-contrib/setup-bazel@0.14.0
        with:
          bazelisk-cache: true
          disk-cache: ${{ github.workflow }}
          repository-cache: true

      - name: Build wheel
        run: bazel build //release:wheel.dist

      - name: Stage wheel (refuse an unversioned build)
        run: |
          mkdir -p dist
          cp bazel-bin/release/wheel_dist/*.whl dist/
          # A tagless/shallow checkout stamps 0.0.0 (workspace_status.sh); never
          # publish that. On a real tag push the version is X.Y.Z.
          if ls dist/*-0.0.0-*.whl >/dev/null 2>&1; then
            echo "::error::wheel version is 0.0.0 — refusing to publish an unversioned build"
            exit 1
          fi

      - name: Wheel actually contains the kernel
        run: |
          python3 -m venv /tmp/icw
          /tmp/icw/bin/pip install dist/*.whl
          /tmp/icw/bin/python -c "import infinite_craft_cli._sudo.craft; print('kernel import OK')"

      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@release/v1
```
Leave the `release` job (changelog extraction + GitHub release) unchanged.

- [ ] **Step 2: `pages.yml` — build the site with Bazel**

Replace the `Generate kernel adapters` + `Bundle trainer` steps with:
```yaml
      - uses: bazel-contrib/setup-bazel@0.14.0
        with:
          bazelisk-cache: true
          disk-cache: ${{ github.workflow }}
          repository-cache: true

      - name: Build site
        run: bazel build //bookmarklet:site
```
and change the `upload-pages-artifact` `path:` from `bookmarklet` to
`bazel-bin/bookmarklet/site`.

- [ ] **Step 3: `test.yml` — add build coverage, drop node setup**

Remove the `actions/setup-node` step (rules_esbuild provides hermetic Node).
After `bazel test //...`, add:
```yaml
      - name: Build release artifacts
        run: bazel build //release:wheel //bookmarklet:site
```

- [ ] **Step 4: `release-dry-run.yml` — repoint to Bazel**

Replace each `bash scripts/generate.sh` (and any `minify.sh`) step with the
matching `bazel build //release:wheel` / `bazel build //bookmarklet:site`
invocation (add a `setup-bazel` step if the job lacks one). Preserve the job's
intent (dry-run of publish/pages).

- [ ] **Step 5: Delete `sudo.yml`**

Run: `git rm .github/workflows/sudo.yml`

- [ ] **Step 6: Lint the workflows**

Run: `for f in .github/workflows/*.yml; do python3 -c "import sys,yaml; yaml.safe_load(open('$f'))" && echo "OK $f"; done`
Expected: every workflow parses (`OK …`). (If PyYAML is unavailable, use
`bazel run` of a yaml check or `actionlint` if installed.)

- [ ] **Step 7: Commit**

```bash
git add .github/workflows/
git commit -m "ci: build wheel + Pages site via Bazel; delete sudo.yml

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Delete the standalone path + committed artifacts + docs

**Files:**
- Delete: `scripts/generate.sh`, `scripts/sudoc-bin.sh`, `scripts/sudoc-version.txt`, `bookmarklet/minify.sh`, `bookmarklet/trainer.js`, `bookmarklet/trainer.min.js`
- Modify: `.gitignore`, `PRIVACY.md`, `CHANGELOG.md`, `tests/parity/run_parity.sh`, `README.md` (drop the `minify.sh`/`generate.sh` build instructions, ~line 146), `tests/parity/README.md` (drop the `generate.sh` fallback mention), `bookmarklet/trainer.src.mjs` (header comment references `scripts/generate.sh`)

**Interfaces:**
- Consumes: nothing new. This task removes the now-dead second path.

- [ ] **Step 1: Delete the standalone scripts + committed bundle**

Run:
```bash
git rm scripts/generate.sh scripts/sudoc-bin.sh scripts/sudoc-version.txt \
       bookmarklet/minify.sh bookmarklet/trainer.js bookmarklet/trainer.min.js
```
(If `scripts/` is now empty, it disappears with the last file.)

- [ ] **Step 2: `.gitignore` — drop the sudoc cache line**

Remove the `.cache/` block (the sudoc download cache used by the deleted
`sudoc-bin.sh`). Keep the `_sudo/` ignores.

- [ ] **Step 3: `tests/parity/run_parity.sh` — remove the generate.sh fallback**

Delete the `if adapters missing … bash scripts/generate.sh` block. If the script
now only wraps `bazel test //tests/parity:parity_test`, replace its body with
that single invocation; otherwise delete the script and its references.

- [ ] **Step 4: `PRIVACY.md` + `CHANGELOG.md` — update the trust model**

In `PRIVACY.md`, replace the "compare served `trainer.min.js` against the
committed artifact" guidance with: the served file is rebuilt by CI from source
on every deploy — `sudo/craft.sudo` transpiled via the pinned rules_sudo release
plus `trainer.src.mjs`, bundled by `//bookmarklet:trainer_min_js` — so there is
no committed artifact to compare against; reproducibility comes from rebuilding
the Bazel target. Add a `CHANGELOG.md` entry describing the consolidation
(single Bazel build path; `trainer.js`/`min.js` now build-only).

Also drop the now-stale build instructions elsewhere: in `README.md` remove the
`scripts/generate.sh` / `bookmarklet/minify.sh` steps (~line 146) and replace
with the Bazel equivalents (`bazel build //bookmarklet:site`,
`bazel build //release:wheel.dist`); in `tests/parity/README.md` remove the
"regenerates adapters via `scripts/generate.sh` if missing" note; in
`bookmarklet/trainer.src.mjs` fix the header comment that points at
`scripts/generate.sh` (adapters now come from `//bookmarklet:_sudo`).

- [ ] **Step 5: Grep for dangling references**

Run:
```bash
grep -rn "generate.sh\|sudoc-bin\|sudoc-version\|minify.sh\|trainer\.min\.js\|trainer\.js" \
  --include=*.md --include=*.yml --include=*.sh --include=*.py --include=*.bazel --include=*.mjs --include=*.js . \
  | grep -v bazel-out | grep -v "bookmarklet/BUILD.bazel\|bookmarklet/esbuild.config.mjs\|tests/artifact_paths\|:trainer_js\|:trainer_min_js\|trainer_js_path\|trainer_min_js_path\|fetch('trainer.min.js'\|docs/superpowers"
```
Expected: no references to the deleted scripts or to reading committed
`trainer.js`/`min.js` as files (only the Bazel targets / locators / the
extension's `fetch('trainer.min.js')` remain). The `--include=*.mjs` catches the
`trainer.src.mjs` header comment; `*.js` catches any lingering loader/userscript
mention.

- [ ] **Step 6: Final green**

Run: `bazel test //... --test_output=errors && bazel build //release:wheel //bookmarklet:site --stamp`
Expected: all tests PASS; both artifacts build. This is the whole system on one
Bazel path.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "chore: delete standalone sudoc path + committed bundle; update trust model

One toolchain (rules_sudo v0.3.0), one build system. generate.sh / sudoc-bin.sh
/ sudoc-version.txt / minify.sh / sudo.yml removed; trainer.js + trainer.min.js
are now build-only.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Wheel (§A) → Tasks 1–2. Bundle (§B) → Task 3. Site + smoke (§B) → Task 4. Test rewiring (§C) → Task 5. CI (§D) → Task 6. Deletions + PRIVACY/CHANGELOG/.gitignore/run_parity (§E) → Task 7. All five risks (§Risks) map to explicit verification steps: py_wheel tree artifact (T2 S3 + fallback), version stamping (T1 S4, T2 S2), minify sentinels (T3 S5), runfiles locator (T5 S1/S6), hermetic Node in CI (T6 S3).
- Rollout order in the spec matches Task order 1→7 (additive first, deletions last).

**Placeholder scan:** No "TBD/TODO/handle edge cases". The two "Note for the
implementer" blocks (py_wheel attr names; rules_esbuild version/API) are
**verification instructions with concrete fallbacks**, required because exact
Bazel-registry APIs are version-sensitive — not deferred work.

**Type consistency:** Locator names `trainer_js_path()` / `trainer_min_js_path()`
are defined in Task 5 Step 1 and used identically in Steps 3–5. Target labels
`//release:wheel`, `//bookmarklet:{trainer_js,trainer_min_js,site,kernel_smoke_test}`
are consistent across Tasks 2–7. Stamp key `STABLE_VERSION` defined in Task 1,
consumed in Task 2.

**Fable review incorporated (2026-08-04):** (1) `py_wheel` packages only
`ctx.files.deps` (no PyInfo traversal) → `_sudo_files` filegroup is now a
first-class wheel dep, not a fallback; bogus `extra_requires` line removed. (2)
Stamped wheel filename is only real on the implicit `//release:wheel.dist` target
→ build/stage/publish all read `bazel-bin/release/wheel_dist/`. (3) `aspect_rules_js`
and `aspect_bazel_lib` get explicit root `bazel_dep`s (bzlmod won't load transitive
repos). (4) `esbuild.config.mjs` uses `export default` (ESM), not `module.exports`.
Pre-empts: root `exports_files`, `0.0.0` tagless fallback + publish guard,
esbuild-version-not-in-table fallback, and Task 7 now also fixes `README.md` /
`tests/parity/README.md` / `trainer.src.mjs` and greps `.mjs`/`.js`.
