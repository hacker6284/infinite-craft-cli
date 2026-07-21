#!/usr/bin/env bash
# Host-parity lockstep: local convenience wrapper. The actual comparator
# lives in tests/parity/parity_test.py (also runnable as a Bazel py_test,
# //tests/parity:parity_test) — this script does not reimplement it.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

if [ ! -d "src/infinite_craft_cli/_sudo" ] || [ ! -d "bookmarklet/_sudo" ]; then
  echo "Generated kernel adapters missing — regenerating via scripts/generate.sh" >&2
  bash scripts/generate.sh
fi

# Mirror sudo.yml's former "Python tests" / host-parity uv invocation:
# pytest is not a pyproject dependency — inject it ephemerally.
exec uv run --with pytest --with pytest-asyncio pytest tests/parity/parity_test.py -q
