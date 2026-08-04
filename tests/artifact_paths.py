"""Locate Bazel-built artifacts (trainer bundle, extension zip) from tests.

Under `bazel test` the files arrive as runfiles data deps; under a plain
`pytest` dev run they are read from `bazel-bin/` (build them first with
`bazel build //bookmarklet:trainer_js //bookmarklet:trainer_min_js //extension:zip`).
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


def extension_zip_path() -> Path:
    return _runfiles_path("extension/infinite-craft-trainer.zip") or _bin_path(
        "extension/infinite-craft-trainer.zip"
    )
