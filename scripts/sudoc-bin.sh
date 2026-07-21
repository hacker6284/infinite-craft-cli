#!/usr/bin/env bash
# Resolves a path to an executable sudoc binary and prints it on stdout
# (only the path — all diagnostics go to stderr, so callers can safely do
# `SUDOC_BIN="$(bash scripts/sudoc-bin.sh)"`). Single acquisition mechanism
# shared by scripts/generate.sh and any CI step that needs to invoke sudoc
# directly (e.g. sudo.yml's kernel lockstep).
#
# Precedence:
#   (a) $SUDOC_BIN, if already set and points at an executable file — an
#       escape hatch for local dev against a sudocode checkout.
#   (b) a cached download at .cache/sudoc/<version>/sudoc.
#   (c) download from the pinned GitHub release for the detected platform,
#       verify its sha256 against scripts/sudoc-version.txt, chmod +x, and
#       cache it. A checksum mismatch is a hard error.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION_FILE="$REPO_ROOT/scripts/sudoc-version.txt"

if [ -n "${SUDOC_BIN:-}" ]; then
    if [ -x "$SUDOC_BIN" ]; then
        echo "$SUDOC_BIN"
        exit 0
    fi
    echo "error: SUDOC_BIN is set but not an executable file: $SUDOC_BIN" >&2
    exit 1
fi

if [ ! -f "$VERSION_FILE" ]; then
    echo "error: missing $VERSION_FILE" >&2
    exit 1
fi

VERSION="$(awk '$1 == "version" { print $2 }' "$VERSION_FILE")"
if [ -z "$VERSION" ]; then
    echo "error: could not read version from $VERSION_FILE" >&2
    exit 1
fi

OS="$(uname -s)"
ARCH="$(uname -m)"
case "$OS-$ARCH" in
    Darwin-arm64)   TARGET="aarch64-apple-darwin" ;;
    Linux-x86_64)   TARGET="x86_64-unknown-linux-gnu" ;;
    Linux-aarch64)  TARGET="aarch64-unknown-linux-gnu" ;;
    *)
        echo "error: unsupported platform $OS-$ARCH (no pinned sudoc release for it)" >&2
        exit 1
        ;;
esac

CACHE_DIR="$REPO_ROOT/.cache/sudoc/$VERSION"
CACHE_BIN="$CACHE_DIR/sudoc"

if [ -x "$CACHE_BIN" ]; then
    echo "$CACHE_BIN"
    exit 0
fi

EXPECTED_SHA="$(awk -v t="$TARGET" '$1 == t { print $2 }' "$VERSION_FILE")"
if [ -z "$EXPECTED_SHA" ]; then
    echo "error: no pinned sha256 for target $TARGET in $VERSION_FILE" >&2
    exit 1
fi

ASSET="sudoc-$TARGET"
URL="https://github.com/hacker6284/sudocode/releases/download/$VERSION/$ASSET"

mkdir -p "$CACHE_DIR"
TMP_BIN="$(mktemp "$CACHE_DIR/.download.XXXXXX")"
trap 'rm -f "$TMP_BIN"' EXIT

echo "sudoc-bin: downloading $URL" >&2
if command -v curl >/dev/null 2>&1; then
    curl -fsSL -o "$TMP_BIN" "$URL"
elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$TMP_BIN" "$URL"
else
    echo "error: neither curl nor wget is available to download sudoc" >&2
    exit 1
fi

if command -v shasum >/dev/null 2>&1; then
    ACTUAL_SHA="$(shasum -a 256 "$TMP_BIN" | awk '{print $1}')"
elif command -v sha256sum >/dev/null 2>&1; then
    ACTUAL_SHA="$(sha256sum "$TMP_BIN" | awk '{print $1}')"
else
    echo "error: neither shasum nor sha256sum is available to verify sudoc" >&2
    exit 1
fi

if [ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]; then
    echo "error: sha256 mismatch for $ASSET" >&2
    echo "  expected: $EXPECTED_SHA" >&2
    echo "  actual:   $ACTUAL_SHA" >&2
    exit 1
fi

chmod +x "$TMP_BIN"
mv "$TMP_BIN" "$CACHE_BIN"
trap - EXIT

echo "$CACHE_BIN"
