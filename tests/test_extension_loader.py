"""Static and artifact checks for the Chrome extension thin loader."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extension"
BOOKMARKLET = ROOT / "bookmarklet"
TRAINER_URL = "https://hacker6284.github.io/infinite-craft-cli/trainer.min.js"
TERSER_VERSION = "5.48.0"
LOADER = EXTENSION / "loader.js"
MANIFEST = EXTENSION / "manifest.json"
TRAINER_JS = BOOKMARKLET / "trainer.js"
TRAINER_SRC = BOOKMARKLET / "trainer.src.mjs"
TRAINER_MIN = BOOKMARKLET / "trainer.min.js"
INDEX_HTML = BOOKMARKLET / "index.html"
ZIP_PATH = ROOT / "infinite-craft-trainer.zip"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.mark.skipif(shutil.which("node") is None, reason="node required")
def test_loader_js_syntax() -> None:
    result = subprocess.run(
        ["node", "--check", str(LOADER)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_trainer_min_js_exists() -> None:
    assert TRAINER_MIN.is_file()
    assert TRAINER_MIN.stat().st_size > 0


def test_extension_does_not_bundle_trainer() -> None:
    assert not (EXTENSION / "trainer.js").exists()


def test_manifest_fetches_hosted_trainer() -> None:
    manifest = json.loads(_read(MANIFEST))
    host_permissions = manifest.get("host_permissions", [])
    assert "https://hacker6284.github.io/*" in host_permissions
    web_accessible = manifest.get("web_accessible_resources", [])
    assert any(
        "page-bridge.js" in entry.get("resources", [])
        for entry in web_accessible
    )

    content_script = manifest["content_scripts"][0]
    assert content_script["js"] == ["loader.js"]
    assert content_script["matches"] == ["https://neal.fun/infinite-craft/*"]
    assert content_script["run_at"] == "document_idle"
    assert manifest["version"] == "1.5.0"


def test_loader_contract() -> None:
    loader = _read(LOADER)
    assert TRAINER_URL in loader
    assert 'cache: "no-store"' in loader
    assert "AbortSignal.timeout" in loader
    assert "MAX_RETRIES" in loader
    assert "RETRY_BASE_MS" in loader
    assert "validatePayload" in loader
    assert "__ICTrainer" in loader
    assert "Missing Content-Type header" in loader
    assert "response.ok" in loader
    assert "Content-Type" in loader
    assert "MAX_BYTES" in loader
    assert "validateContentLength" in loader
    assert "Content-Length" in loader
    assert "const ready = waitForTrainerReady" in loader
    assert "injectTrainer(code)" in loader
    assert 'if (!document.getElementById("ict-container"))' in loader
    assert "await ready" in loader
    assert "injectTrainer" in loader
    assert "page-bridge.js" in loader
    assert "chrome.runtime.getURL" in loader
    assert "ict-inject-trainer" in loader
    assert "ensurePageBridge" in loader
    assert "waitForTrainerReady" in loader
    assert "ict-trainer-ready" in loader
    assert 'getElementById("ict-container")' in loader
    assert "maybeStartLoader" in loader
    assert 'loaderState = "loading"' in loader
    assert 'loaderState = "loaded"' in loader
    assert "loaderState = null" in loader
    assert "pageshow" in loader
    assert "event.persisted" in loader
    assert "console.error" in loader
    assert "dataset.ictLoader" not in loader
    assert "waitForTrainerUI" not in loader
    assert "trainer.js" not in loader


def test_cross_surface_trainer_url_parity() -> None:
    loader = _read(LOADER)
    index_html = _read(INDEX_HTML)
    userscript = _read(BOOKMARKLET / "trainer.user.js")

    assert TRAINER_URL in loader
    assert "fetch('trainer.min.js'" in index_html
    assert "cache: 'no-store'" in index_html
    assert TRAINER_URL in userscript
    assert "cache: 'no-store'" in userscript
    assert "AbortSignal.timeout" in userscript


@pytest.mark.skipif(shutil.which("node") is None, reason="node required")
def test_trainer_min_js_matches_terser_output() -> None:
    result = subprocess.run(
        ["npx", "--yes", f"terser@{TERSER_VERSION}", str(TRAINER_JS), "-c", "-m"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=ROOT,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == _read(TRAINER_MIN).strip()


def test_extension_zip_matches_current_layout() -> None:
    assert ZIP_PATH.is_file()
    listing = subprocess.run(
        ["unzip", "-l", str(ZIP_PATH)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "trainer.js" not in listing
    assert "loader.js" in listing
    assert "manifest.json" in listing
    assert "icons/icon16.png" in listing


def test_extension_zip_matches_source_hashes() -> None:
    with zipfile.ZipFile(ZIP_PATH) as archive:
        for name in ("loader.js", "manifest.json"):
            archived = archive.read(name)
            source = (EXTENSION / name).read_bytes()
            assert _sha256(archived) == _sha256(source), name


def test_trainer_min_contains_sentinel_and_ui_marker() -> None:
    minified = _read(TRAINER_MIN)
    assert "__ICTrainer" in minified
    assert "ict-container" in minified
    assert "ict-trainer-ready" in minified


def test_trainer_source_has_single_source_comment() -> None:
    source = _read(TRAINER_SRC)
    assert "Single source of truth" in source
    assert source.index("Single source of truth") < source.index("const BASE_ELEMENTS")


def test_trainer_defers_singleton_until_ui_ready() -> None:
    source = _read(TRAINER_JS)
    singleton_set = source.index("window.__ICTrainer = true")
    container_append = source.index("document.body.appendChild(container)")
    ready_event = source.index('new CustomEvent("ict-trainer-ready")')
    assert container_append < singleton_set < ready_event