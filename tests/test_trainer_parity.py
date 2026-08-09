"""Host-facing trainer parity checks that are not covered elsewhere.

Pure command classification, matching, and validation live in craft.sudo
(kernel tests) and in ``tests/parity`` (lockstep Python vs JS host runners).

This file only keeps:
- a structural regression for the browser trainer confirm handler
- host ANSI rendering of kernel validation messages (cli wraps segments)
"""

from __future__ import annotations

import re
import sys

import pytest

from infinite_craft_cli.cli import _validate_command_line
from tests.artifact_paths import trainer_js_path

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


def _strip_ansi(msg: str | None) -> str | None:
    if msg is None:
        return None
    return re.sub(r"\x1b\[[0-9;]*m", "", msg)


class TestTrainerSourceParity:
    def test_wait_for_confirm_uses_try_enqueue(self):
        """Confirm key handler must enqueue non-y/n lines via tryEnqueue (not raw enqueue)."""
        source = trainer_js_path().read_text(encoding="utf-8")
        assert "function tryEnqueue(line)" in source
        assert "tryEnqueue(val)" in source
        handler = re.search(
            r"function waitForConfirmKey\(\)[\s\S]*?function handler\(e\)[\s\S]*?\n      \}",
            source,
        )
        assert handler is not None, "waitForConfirmKey handler not found in trainer.js"
        assert "tryEnqueue(val)" in handler.group(0)
        assert "enqueueCommand(val)" not in handler.group(0)


class TestHostValidationRendering:
    """CLI renders kernel error segments with ANSI; assert user-visible text."""

    @pytest.mark.parametrize(
        "line,expected_substr",
        [
            ("/combine Water Fire", None),
            ("/crawl Water Fire", None),
            ("/cross Water Fire", None),
            ("Water + Fire", None),
            ("/^fi/ * /^wa/", None),
            ("/cross /^fi/ /^wa/", None),
            ("/combine Water + Fire", "positional args"),
            ("/cross fire* * water*", "positional args"),
            ("Water + | Fire", "no space between + and |"),
            ("/combine Water + | Fire", "no space between + and |"),
            ("Water +", "Usage: <element> + <element>"),
            ("/^fi/", "Unknown input"),
            ("/notacommand", "Unknown command"),
            ("Water Fire", "Unknown input"),
            ("/permute", "Usage: /permute <query>"),
            ("/with Water", "Usage: /with <element> <query>"),
            ("Water ++ Fire", None),
            ("/import", "Usage: /import <element>"),
            ("/crawl Banana", "Usage: /crawl <element> <element>"),
        ],
    )
    def test_validation_message(self, line, expected_substr):
        err = _strip_ansi(_validate_command_line(line))
        if expected_substr is None:
            assert err is None
        else:
            assert err is not None
            assert expected_substr in err
