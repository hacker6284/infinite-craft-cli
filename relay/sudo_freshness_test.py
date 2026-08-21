"""Tripwire: the vendored kernel under relay/_sudo must be byte-identical
to the Bazel-generated //bookmarklet:_sudo tree.

The relay deploys straight from git (Render, no build step), so the
generated JS kernel is committed. This test fails the moment craft.sudo
changes without re-vendoring:  cp -R bazel-bin/bookmarklet/_sudo relay/_sudo
"""

import os
import sys


def tree(root: str) -> dict[str, bytes]:
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            out[os.path.relpath(p, root)] = open(p, "rb").read()
    return out


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    vendored = tree(os.path.join(here, "_sudo"))
    generated = tree(os.path.join(here, "..", "bookmarklet", "_sudo"))
    ok = True
    for name in sorted(set(vendored) | set(generated)):
        if name not in vendored:
            print(f"MISSING from relay/_sudo: {name}")
            ok = False
        elif name not in generated:
            print(f"STALE extra file in relay/_sudo: {name}")
            ok = False
        elif vendored[name] != generated[name]:
            print(f"STALE: relay/_sudo/{name} differs from generated kernel")
            ok = False
    if not ok:
        print("re-vendor with: cp -R bazel-bin/bookmarklet/_sudo relay/_sudo")
        return 1
    print(f"relay/_sudo fresh ({len(vendored)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
