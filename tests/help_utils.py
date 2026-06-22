"""Shared help-text assertions for Python CLI, JS trainers, README, and index.html."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEPRECATED_PHRASES = ("also:", "Same as", "spaced variant")

DUAL_FORMAT_COMMANDS = (
    ("<element> + <element>", "/combine <element> <element>"),
    ("<element> ++ <element>", "/crawl <element> <element>"),
    ("<element> +| <query>", "/with <element> <query>"),
    ("<query> * <query>", "/cross <query> <query>"),
)

SHORTHAND_MARKERS = (" + <element>", " ++ <element>", " +| <query>", "<query> * <query>")

QUERY_SYNTAX_MARKER = "Query syntax"


def assert_help_text_clean(text: str) -> None:
    """No deprecated duplicate-format phrases."""
    lower = text.lower()
    for phrase in DEPRECATED_PHRASES:
        assert phrase.lower() not in lower, f"deprecated phrase {phrase!r} found in help"


def assert_help_dual_formats(text: str) -> None:
    """Each dual-format command appears exactly once (shorthand + slash)."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for (shorthand, slash), marker in zip(DUAL_FORMAT_COMMANDS, SHORTHAND_MARKERS):
        shorthand_lines = [ln for ln in lines if marker in ln and not ln.lstrip().startswith("/")]
        slash_lines = [ln for ln in lines if ln.lstrip().startswith(slash.split()[0])]
        assert len(shorthand_lines) == 1, f"expected one shorthand line for {shorthand!r}, got {shorthand_lines!r}"
        assert len(slash_lines) == 1, f"expected one slash line for {slash!r}, got {slash_lines!r}"


def assert_help_query_syntax_once(text: str) -> None:
    """Query syntax section appears once."""
    assert text.count(QUERY_SYNTAX_MARKER) == 1


def extract_js_help_plaintext(trainer_path: Path | None = None) -> str:
    """Extract doHelp() template literals from trainer.js as plain text."""
    path = trainer_path or (ROOT / "bookmarklet" / "trainer.js")
    source = path.read_text(encoding="utf-8")
    match = re.search(
        r"function doHelp\(\) \{\s*print\(`([\s\S]*?)`\);",
        source,
    )
    assert match, "doHelp() template not found"
    body = match.group(1)
    body = re.sub(r"\$\{(?:cyan|bold)\(\"([^\"]*)\"\)\}", r"\1", body)
    body = re.sub(r"\$\{[^}]+\}", "", body)
    return body


def read_readme_commands_section() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


def read_index_html_commands() -> str:
    return (ROOT / "bookmarklet" / "index.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Legacy interactive runner helpers (used by multiple test modules for
# compatibility smoke tests and older direct-drive tests).
# New UI tests should prefer REPLTestHarness from conftest.
# ---------------------------------------------------------------------------

import asyncio
from unittest.mock import patch


def run_async(coro):
    """Run a coroutine with a generous timeout (for legacy interactive tests)."""
    return asyncio.run(asyncio.wait_for(coro, timeout=30.0))


def _run_interactive(inputs):
    """Run interactive_mode with a sequence of input lines (legacy path).
    Forces non-TTY to use the builtins.input fallback.
    """
    from infinite_craft_cli.cli import interactive_mode

    input_iter = iter(inputs + ["/quit"])
    # Patch isatty False to force non-chrome + builtins.input path (legacy compat;
    # tty/chromium path would bypass the input mock and hit _tty_read_line on captured fd)
    with (
        patch("builtins.input", side_effect=input_iter),
        patch("sys.stdout.isatty", return_value=False),
        patch("sys.stdin.isatty", return_value=False),
    ):
        run_async(interactive_mode())
