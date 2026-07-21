#!/usr/bin/env bash
# Regenerate trainer.js (bundled) and trainer.min.js (minified) from
# trainer.src.mjs (requires Node.js + npx).
set -euo pipefail
cd "$(dirname "$0")"
ESBUILD_VERSION="0.28.1"
TERSER_VERSION="5.48.0"
BANNER="/* Built artifact — do not edit. Single source of truth: trainer.src.mjs
 * (UI/effects) + ../sudo/craft.sudo (kernel, transpiled via sudoc). */"
npx --yes "esbuild@${ESBUILD_VERSION}" trainer.src.mjs --bundle --format=iife --banner:js="$BANNER" --outfile=trainer.js
echo "Wrote trainer.js ($(wc -c < trainer.js) bytes) with esbuild@${ESBUILD_VERSION}"
npx --yes "terser@${TERSER_VERSION}" trainer.js -c -m -o trainer.min.js
echo "Wrote trainer.min.js ($(wc -c < trainer.min.js) bytes) with terser@${TERSER_VERSION}"