"""Host-parity lockstep: Python (cli.py) vs JS (trainer.src.mjs) wiring.

Runnable as ``bazel test //tests/parity:parity_test`` or
``pytest tests/parity/parity_test.py`` outside Bazel.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _load_run_py():
    """Load run_py.py from its on-disk sibling path (no package import)."""
    run_py_path = Path(__file__).resolve().parent / "run_py.py"
    spec = importlib.util.spec_from_file_location("parity_run_py", run_py_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load run_py module from {run_py_path}")
    module = importlib.util.module_from_spec(spec)
    # Ensure the module is visible if it re-enters via sys.modules later.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _resolve_data_file(repo_relative_path: str) -> Path:
    """Resolve a bazel ``data``-declared file's on-disk path at test time.

    Tries the Bazel runfiles environment first (RUNFILES_DIR / TEST_SRCDIR,
    bzlmod main-repo runfiles dir name ``_main``, falling back to
    TEST_WORKSPACE if set), then falls back to plain-filesystem resolution
    relative to this file's own real location — the latter makes
    ``pytest tests/parity/parity_test.py`` work identically outside Bazel.
    """
    rel = repo_relative_path.lstrip("/")
    runfiles_root = os.environ.get("RUNFILES_DIR") or os.environ.get("TEST_SRCDIR")
    if runfiles_root:
        candidates = ["_main"]
        test_ws = os.environ.get("TEST_WORKSPACE")
        if test_ws and test_ws not in candidates:
            candidates.append(test_ws)
        for ws in candidates:
            candidate = Path(runfiles_root) / ws / rel
            if candidate.is_file():
                return candidate
        # Some layouts place main-repo files directly under RUNFILES_DIR.
        direct = Path(runfiles_root) / rel
        if direct.is_file():
            return direct

    # Outside Bazel: this file lives at tests/parity/parity_test.py.
    fallback = Path(__file__).resolve().parent.parent.parent / rel
    if fallback.is_file():
        return fallback
    # Sibling-relative for files under tests/parity/.
    sibling = Path(__file__).resolve().parent / Path(rel).name
    if sibling.is_file():
        return sibling
    raise FileNotFoundError(
        f"cannot resolve data file {repo_relative_path!r} "
        f"(runfiles_root={runfiles_root!r})"
    )


def test_python_js_host_parity():
    run_py = _load_run_py()
    py_results = run_py.run_all_scenarios()

    run_js_path = _resolve_data_file("tests/parity/run_js.mjs")
    proc = subprocess.run(
        ["node", str(run_js_path)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"JS host runner exited {proc.returncode}\n"
            f"--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}"
        )

    try:
        js_results = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"JS host runner produced invalid JSON: {exc}\n"
            f"--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}"
        ) from exc

    all_ids = sorted(set(py_results) | set(js_results))
    assert len(all_ids) > 0, "no scenarios ran (empty fixtures?)"

    failures = []
    for sid in all_ids:
        if sid not in py_results:
            failures.append((sid, "MISSING from Python output", None, js_results[sid]))
            continue
        if sid not in js_results:
            failures.append((sid, "MISSING from JS output", py_results[sid], None))
            continue
        if py_results[sid] != js_results[sid]:
            failures.append((sid, "MISMATCH", py_results[sid], js_results[sid]))

    if failures:
        blocks = []
        for sid, reason, py_val, js_val in failures:
            blocks.append(
                f"--- {sid} ({reason}) ---\n"
                f"  python: {json.dumps(py_val, indent=2, ensure_ascii=False)}\n"
                f"  js:     {json.dumps(js_val, indent=2, ensure_ascii=False)}"
            )
        raise AssertionError(
            f"{len(failures)} of {len(all_ids)} scenarios failed:\n\n"
            + "\n\n".join(blocks)
        )


# Run with pytest when invoked directly by Bazel
if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
