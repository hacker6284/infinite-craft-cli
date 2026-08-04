#!/usr/bin/env bash
# Pre-release HEAD dogfood (design §8 Phase 5 Task 8): build the sibling sudocode
# checkout's HEAD matched binary set (sudoc + lockstep_diff + capture_run +
# emit_unpack), point this repo's MODULE.bazel `sudo.local_binary` at them, and
# run the craft lockstep test against the rules_sudo 1.0.0 hermetic API — WITHOUT
# needing any published release. After the matched-pair release, MODULE.bazel
# swaps back to archive_override + sudo.toolchain(version=...) and this script
# is retired.
#
# The absolute HEAD paths are machine-specific, so MODULE.bazel is committed with
# /REPLACE/... placeholders; this script fills them for the test run and RESTORES
# the placeholders on exit (leaving the committed file clean).
set -euo pipefail

here="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"                 # infinite-craft-cli root
module="$repo/MODULE.bazel"
sudocode="${SUDOCODE_REPO:-$(cd "$repo/../sudocode" && pwd)}"

echo "==> Building HEAD binaries in $sudocode"
(cd "$sudocode" && bazel build -c opt \
  //sudoc/crates/cli:sudoc \
  //sudoc/crates/harness:lockstep_diff \
  //sudoc/crates/harness:capture_run \
  //sudoc/crates/harness:emit_unpack)

sudoc="$(cd "$sudocode" && realpath "$(bazel cquery -c opt --output=files //sudoc/crates/cli:sudoc 2>/dev/null)")"
diff="$(cd "$sudocode" && realpath "$(bazel cquery -c opt --output=files //sudoc/crates/harness:lockstep_diff 2>/dev/null)")"
cap="$(cd "$sudocode" && realpath "$(bazel cquery -c opt --output=files //sudoc/crates/harness:capture_run 2>/dev/null)")"
unp="$(cd "$sudocode" && realpath "$(bazel cquery -c opt --output=files //sudoc/crates/harness:emit_unpack 2>/dev/null)")"

# Restore the committed placeholders no matter how we exit.
backup="$(mktemp)"
cp "$module" "$backup"
trap 'cp "$backup" "$module"; rm -f "$backup"; echo "==> Restored MODULE.bazel placeholders"' EXIT

echo "==> Wiring MODULE.bazel local_binary at HEAD binaries"
python3 - "$module" "$sudoc" "$diff" "$cap" "$unp" <<'PY'
import re, sys
path, sudoc, diff, cap, unp = sys.argv[1:6]
s = open(path).read()
s = re.sub(r'sudoc = "[^"]*"', 'sudoc = "%s"' % sudoc, s, count=1)
s = re.sub(r'lockstep_diff = "[^"]*"', 'lockstep_diff = "%s"' % diff, s, count=1)
s = re.sub(r'capture_run = "[^"]*"', 'capture_run = "%s"' % cap, s, count=1)
s = re.sub(r'emit_unpack = "[^"]*"', 'emit_unpack = "%s"' % unp, s, count=1)
open(path, "w").write(s)
PY

echo "==> Running craft lockstep test (py + js) against HEAD binaries"
(cd "$repo" && bazel test //sudo:craft_lockstep_test --test_output=errors "$@")
echo "==> PASS: craft.sudo lockstep-agrees py<->js via rules_sudo 1.0.0 (HEAD dogfood)."
