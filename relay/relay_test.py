"""Bazel wrapper for the relay's node:test suite (same system-node
compromise as //tests/parity — no JS ruleset for one small service)."""

import os
import subprocess
import sys


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    proc = subprocess.run(
        ["node", "--test", "server.test.mjs"],
        cwd=here,
    )
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
