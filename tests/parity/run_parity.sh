#!/usr/bin/env bash
# Host-parity lockstep: local convenience wrapper. The actual comparator
# lives in tests/parity/parity_test.py (also runnable as a Bazel py_test,
# //tests/parity:parity_test) — this script does not reimplement it.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

# Kernel adapters are Bazel-generated targets now (//src/infinite_craft_cli:_sudo,
# //bookmarklet:_sudo) — no standalone regeneration step needed.
exec bazel test //tests/parity:parity_test --test_output=errors
