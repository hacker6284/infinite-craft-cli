"""Thin contract checks for the Chrome extension loader (not source-identifier soup)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extension"
LOADER = EXTENSION / "loader.js"
MANIFEST = EXTENSION / "manifest.json"
TRAINER_URL = "https://hacker6284.github.io/infinite-craft-cli/trainer.min.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node required")
def test_loader_js_syntax() -> None:
    result = subprocess.run(
        ["node", "--check", str(LOADER)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_extension_does_not_bundle_trainer() -> None:
    assert not (EXTENSION / "trainer.js").exists()


def test_manifest_fetches_hosted_trainer() -> None:
    manifest = json.loads(_read(MANIFEST))
    host_permissions = manifest.get("host_permissions", [])
    assert "https://hacker6284.github.io/*" in host_permissions
    web_accessible = manifest.get("web_accessible_resources", [])
    assert any(
        "page-bridge.js" in entry.get("resources", []) for entry in web_accessible
    )
    content_script = manifest["content_scripts"][0]
    assert content_script["js"] == ["loader.js"]
    assert content_script["matches"] == ["https://neal.fun/infinite-craft/*"]
    assert content_script["run_at"] == "document_idle"


def test_loader_fetches_hosted_url() -> None:
    """Loader must pull the hosted trainer, not a bundled trainer.js."""
    loader = _read(LOADER)
    assert TRAINER_URL in loader
    assert "trainer.js" not in loader
