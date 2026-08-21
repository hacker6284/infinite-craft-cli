"""Docs-drift tripwire: every documentation surface must cover the kernel's
canonical command inventory (v2.1.1).

Four surfaces: README.md, the CLI /help text, the trainer /help text, and
the GitHub Pages site (bookmarklet/index.html). Adding a command to the
kernel without documenting it everywhere fails this test; so does leaving
removed syntax documented.
"""

from pathlib import Path

from infinite_craft_cli._sudo import craft

REPO = Path(__file__).resolve().parents[1]

# Documented per-surface exemptions (host-specific commands).
CLI_ONLY = {"/queue", "/quit"}
TRAINER_ONLY = set()

# Features beyond slash commands that every surface must mention.
FEATURE_LITERALS = [
    "(expr)*",        # postfix family
    "(expr)100",      # take/sample
    ":=",             # walrus
    "[ expr ]",       # new-elements set
    "->",             # until loop
]


def _surfaces():
    return {
        "README.md": (REPO / "README.md").read_text(encoding="utf-8"),
        "cli /help": (REPO / "src/infinite_craft_cli/cli.py").read_text(encoding="utf-8"),
        "trainer /help": (REPO / "bookmarklet/trainer.src.mjs").read_text(encoding="utf-8"),
        "pages site": (REPO / "bookmarklet/index.html").read_text(encoding="utf-8"),
    }


def test_every_known_command_documented_everywhere():
    commands = list(craft.known_slash_commands())
    assert len(commands) >= 23
    missing = []
    for name, text in _surfaces().items():
        for cmd in commands:
            if cmd in CLI_ONLY and name in ("trainer /help", "pages site"):
                continue
            if cmd not in text:
                missing.append((name, cmd))
    assert not missing, f"undocumented commands: {missing}"


def test_feature_literals_documented_everywhere():
    import html

    missing = []
    for name, text in _surfaces().items():
        for lit in FEATURE_LITERALS:
            if lit not in text and html.escape(lit) not in text:
                missing.append((name, lit))
    assert not missing, f"undocumented features: {missing}"


def test_removed_syntax_not_documented():
    # `+|` was removed in 2.0 — it may appear in migration notes (README/
    # CHANGELOG) but never as a documented command form on the site or in
    # either /help text.
    for name in ("trainer /help", "pages site"):
        text = _surfaces()[name]
        assert "+| <query>" not in text and "+| &lt;query&gt;" not in text, (
            f"{name} still documents the removed +| shorthand"
        )


def test_site_does_not_tell_users_to_refresh():
    # Live page sync shipped in 1.10.0; the old "refresh the page to see
    # elements" tip must never come back.
    text = _surfaces()["pages site"]
    assert "refresh the page" not in text.lower()


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
