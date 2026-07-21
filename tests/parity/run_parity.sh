#!/usr/bin/env bash
# Host-parity lockstep: drives BOTH hosts' hand-written kernel-wiring code
# (src/infinite_craft_cli/cli.py and bookmarklet/trainer.src.mjs) with the
# identical scenarios in fixtures.json, and diffs their canonicalized JSON
# output. Catches bugs in each host's OWN glue code around the shared
# sudo-generated kernel — the kernel itself is covered separately by
# `sudoc test --target py --target js sudo/craft.sudo` (kernel lockstep),
# which never exercises either host's wiring layer at all.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

if [ ! -d "src/infinite_craft_cli/_sudo" ] || [ ! -d "bookmarklet/_sudo" ]; then
  echo "Generated kernel adapters missing — regenerating via scripts/generate.sh" >&2
  bash scripts/generate.sh
fi

PY_OUT="$(mktemp -t parity-py.XXXXXX)"
JS_OUT="$(mktemp -t parity-js.XXXXXX)"
trap 'rm -f "$PY_OUT" "$JS_OUT"' EXIT

echo "Running Python host wiring..." >&2
uv run python tests/parity/run_py.py > "$PY_OUT"

echo "Running JS host wiring..." >&2
node tests/parity/run_js.mjs > "$JS_OUT"

python3 - "$PY_OUT" "$JS_OUT" << 'PYEOF'
import json
import sys

py_path, js_path = sys.argv[1], sys.argv[2]
with open(py_path, encoding="utf-8") as f:
    py = json.load(f)
with open(js_path, encoding="utf-8") as f:
    js = json.load(f)

all_ids = sorted(set(py) | set(js))
failures = []
for sid in all_ids:
    if sid not in py:
        failures.append((sid, "MISSING from Python output", None, js[sid]))
        continue
    if sid not in js:
        failures.append((sid, "MISSING from JS output", py[sid], None))
        continue
    if py[sid] != js[sid]:
        failures.append((sid, "MISMATCH", py[sid], js[sid]))

print(f"\n{len(all_ids)} scenarios compared.")
if failures:
    print(f"{len(failures)} FAILED:\n")
    for sid, reason, py_val, js_val in failures:
        print(f"--- {sid} ({reason}) ---")
        print("  python:", json.dumps(py_val, indent=2, ensure_ascii=False))
        print("  js:    ", json.dumps(js_val, indent=2, ensure_ascii=False))
        print()
    sys.exit(1)
else:
    print(f"All {len(all_ids)} scenarios PASS.")
    sys.exit(0)
PYEOF
PYEOF_STATUS=$?

exit "$PYEOF_STATUS"
