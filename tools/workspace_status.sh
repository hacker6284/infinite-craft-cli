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
