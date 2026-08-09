#!/usr/bin/env python3
"""Open neal.fun/infinite-craft and inject local trainer.js via Playwright.

Default: reuse a long-lived Chromium (CDP) — reload the page and re-inject.
Only starts a new browser if none is listening. Close the window when done.

Usage (from repo root):
  bazel build //bookmarklet:trainer_js
  python3 bookmarklet/launch_neal.py              # start or refresh+reinject
  python3 bookmarklet/launch_neal.py --reload     # fail if browser not up
  python3 bookmarklet/launch_neal.py --fresh      # kill profile browser, start new
  python3 bookmarklet/launch_neal.py --build      # bazel build trainer first

Optional:
  TRAINER_JS=path/to/trainer.js python3 bookmarklet/launch_neal.py
  ICT_CDP_PORT=9333 python3 bookmarklet/launch_neal.py
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STATE_DIR = REPO / ".cache" / "ict-neal"
CDP_PORT = int(os.environ.get("ICT_CDP_PORT", "9333"))
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
META_PATH = STATE_DIR / "cdp.json"
PROFILE_DIR = STATE_DIR / "chrome-profile"
DEFAULT_URL = "https://neal.fun/infinite-craft/"


def find_trainer(prefer_min: bool) -> Path:
    env = os.environ.get("TRAINER_JS")
    if env:
        p = Path(env).expanduser().resolve()
        if not p.is_file():
            sys.exit(f"TRAINER_JS not found: {p}")
        return p

    candidates = []
    if prefer_min:
        candidates += [
            REPO / "bazel-bin" / "bookmarklet" / "trainer.min.js",
            REPO / "bookmarklet" / "trainer.min.js",
        ]
    candidates += [
        REPO / "bazel-bin" / "bookmarklet" / "trainer.js",
        REPO / "bookmarklet" / "trainer.js",
    ]
    for p in candidates:
        if p.is_file():
            return p
    sys.exit(
        "No trainer.js found. Build first:\n"
        "  bazel build //bookmarklet:trainer_js\n"
        "Or: python3 bookmarklet/launch_neal.py --build\n"
        "Or set TRAINER_JS=/path/to/trainer.js"
    )


def ensure_playwright():
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        print("Installing playwright Python package…", file=sys.stderr)
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "playwright", "-q"]
        )
    from playwright.sync_api import sync_playwright

    return sync_playwright


def cdp_alive(port: int = CDP_PORT, timeout: float = 0.8) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version", timeout=timeout
        ) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def chromium_executable() -> str:
    sync_playwright = ensure_playwright()
    with sync_playwright() as p:
        return p.chromium.executable_path


def write_meta(pid: int, port: int) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    META_PATH.write_text(
        json.dumps({"pid": pid, "port": port, "cdp": f"http://127.0.0.1:{port}"}),
        encoding="utf-8",
    )


def read_meta() -> dict | None:
    if not META_PATH.is_file():
        return None
    try:
        return json.loads(META_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def stop_browser() -> None:
    """Best-effort stop of the CDP Chromium we spawned."""
    meta = read_meta()
    if meta and isinstance(meta.get("pid"), int):
        pid = meta["pid"]
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.4)
            try:
                os.kill(pid, 0)
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
            print(f"Stopped Chromium pid={pid}")
        except OSError:
            pass
    if META_PATH.is_file():
        with contextlib.suppress(OSError):
            META_PATH.unlink()


def start_browser(url: str, port: int = CDP_PORT) -> None:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    exe = chromium_executable()
    cmd = [
        exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "--window-size=1280,900",
        url,
    ]
    print(f"Starting Chromium (CDP port {port}) …")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # survive parent exit
    )
    write_meta(proc.pid, port)
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if cdp_alive(port):
            print(f"Chromium ready at {CDP_URL} (pid={proc.pid})")
            return
        if proc.poll() is not None:
            sys.exit(f"Chromium exited early with code {proc.returncode}")
        time.sleep(0.15)
    sys.exit(f"Timed out waiting for CDP at {CDP_URL}")


def load_trainer_code(prefer_min: bool) -> tuple[Path, str]:
    trainer_path = find_trainer(prefer_min=prefer_min)
    code = trainer_path.read_text(encoding="utf-8")
    if "__ICTrainer" not in code and "initBrowserUI" not in code:
        print(
            f"Warning: {trainer_path} may not be a trainer bundle "
            f"(no __ICTrainer / initBrowserUI sentinel).",
            file=sys.stderr,
        )
    print(f"Trainer: {trainer_path} ({len(code):,} bytes)")
    return trainer_path, code


def inject_into_page(page, code: str, url: str) -> None:
    """Reload game page (or navigate) and inject trainer in page world."""
    current = ""
    try:
        current = page.url or ""
    except Exception:
        current = ""
    if "neal.fun/infinite-craft" in current:
        print("Reloading page …")
        page.reload(wait_until="domcontentloaded", timeout=60_000)
    else:
        print(f"Opening {url} …")
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:
        pass
    time.sleep(1.0)

    print("Injecting trainer into page context…")
    # Clear prior singleton if a soft inject left it (full reload usually wipes it).
    page.evaluate(
        """() => {
          try { delete window.__ICTrainer; } catch (e) {}
          const el = document.getElementById("ict-container");
          if (el) el.remove();
        }"""
    )
    # CDP evaluate runs in the page world (not blocked by CSP like inline script).
    page.evaluate(code)

    try:
        page.wait_for_selector("#ict-container", timeout=15_000)
        print("Trainer UI mounted (#ict-container).")
    except Exception:
        print(
            "Warning: #ict-container not found — check the browser console.",
            file=sys.stderr,
        )


def refresh_and_inject(*, prefer_min: bool, url: str) -> None:
    """Connect over CDP, reload, reinject. Does not close the browser."""
    _, code = load_trainer_code(prefer_min)
    sync_playwright = ensure_playwright()
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        # Prefer an existing page in the default context.
        if not browser.contexts:
            context = browser.new_context(
                viewport={"width": 1280, "height": 900},
            )
        else:
            context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()
        inject_into_page(page, code, url)
        # Intentionally do NOT browser.close() — disconnect only.
    print("Done (browser left open). Run again to rebuild + refresh.")


def maybe_build() -> None:
    print("Building //bookmarklet:trainer_js …")
    subprocess.check_call(
        ["bazel", "build", "//bookmarklet:trainer_js"],
        cwd=str(REPO),
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--min",
        action="store_true",
        help="Prefer trainer.min.js over trainer.js",
    )
    ap.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"Page to open (default: {DEFAULT_URL})",
    )
    ap.add_argument(
        "--reload",
        action="store_true",
        help="Only refresh+reinject; error if Chromium CDP is not up",
    )
    ap.add_argument(
        "--fresh",
        action="store_true",
        help="Stop existing CDP Chromium and start a new one",
    )
    ap.add_argument(
        "--build",
        action="store_true",
        help="Run bazel build //bookmarklet:trainer_js before inject",
    )
    ap.add_argument(
        "--stop",
        action="store_true",
        help="Stop the CDP Chromium and exit",
    )
    args = ap.parse_args()

    if args.stop:
        stop_browser()
        return 0

    if args.build:
        maybe_build()

    if args.fresh:
        stop_browser()
        time.sleep(0.3)

    alive = cdp_alive()
    if args.reload and not alive:
        sys.exit(
            f"No Chromium on {CDP_URL}. Start once with:\n"
            f"  python3 bookmarklet/launch_neal.py"
        )

    if not alive:
        start_browser(args.url, CDP_PORT)
    else:
        print(f"Reusing Chromium at {CDP_URL}")

    refresh_and_inject(prefer_min=args.min, url=args.url)
    print()
    print("Try in the trainer prompt:")
    print("  /help   /list   Water + Fire   Water ++ Fire")
    print("  (next change: bazel build + python3 bookmarklet/launch_neal.py)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
