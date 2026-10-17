"""Headless-Chrome lab: trainer combine vs game combine, hive mocked.

The game always hits GET /api/infinite-craft/pair. The trainer goes
pairCache → hive lookup → that same URL. This file drives the built
trainer.js in Chromium against a local page that owns IndexedDB and a
game Combine button.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from infinite_craft_cli._sudo import craft

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

FIRST = "Brokeback Fri-fries Island"
SECOND = "Cowboy"
PRODUCT = "Brokeback Mountain"
PRODUCT_EMOJI = "🏔️"
COMBINE_LINE = f"{FIRST} + {SECOND}"


def _resolve_data_file(repo_relative_path: str) -> Path:
    rel = repo_relative_path.lstrip("/")
    runfiles_root = os.environ.get("RUNFILES_DIR") or os.environ.get("TEST_SRCDIR")
    if runfiles_root:
        candidates = ["_main"]
        test_ws = os.environ.get("TEST_WORKSPACE")
        if test_ws and test_ws not in candidates:
            candidates.append(test_ws)
        for ws in candidates:
            candidate = Path(runfiles_root) / ws / rel
            if candidate.is_file():
                return candidate
        direct = Path(runfiles_root) / rel
        if direct.is_file():
            return direct
    here = Path(__file__).resolve().parent
    sibling = here / Path(rel).name
    if sibling.is_file():
        return sibling
    repo = here.parent.parent
    fallback = repo / rel
    if fallback.is_file():
        return fallback
    built = repo / "bazel-bin" / rel
    if built.is_file():
        return built
    raise FileNotFoundError(
        f"cannot resolve {repo_relative_path!r} (runfiles_root={runfiles_root!r})"
    )


def _pair_key(a: str, b: str) -> str:
    ka, kb = craft.pair_key(a, b)
    return f"{ka}\0{kb}"


@dataclass
class LabState:
    hive_hits: dict[str, dict] = field(default_factory=dict)
    pair_body: dict = field(
        default_factory=lambda: {
            "result": PRODUCT,
            "emoji": PRODUCT_EMOJI,
            "isNew": False,
        }
    )
    pair_requests: list[dict] = field(default_factory=list)
    hive_requests: list[dict] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def record_pair(self, first: str, second: str) -> None:
        with self.lock:
            self.pair_requests.append({"first": first, "second": second})

    def record_hive(self, method: str, path: str, body: object | None = None) -> None:
        with self.lock:
            self.hive_requests.append({"method": method, "path": path, "body": body})

    def pair_count(self) -> int:
        with self.lock:
            return len(self.pair_requests)

    def hive_paths(self) -> list[str]:
        with self.lock:
            return [r["path"] for r in self.hive_requests]


def _json_bytes(handler: BaseHTTPRequestHandler, code: int, payload: dict) -> None:
    raw = json.dumps(payload).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(raw)


def _read_json(handler: BaseHTTPRequestHandler) -> object:
    length = int(handler.headers.get("Content-Length") or "0")
    raw = handler.rfile.read(length) if length else b""
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def _make_handler(state: LabState, page_html: bytes, trainer_js: bytes):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.end_headers()

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if path in ("/", "/index.html"):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page_html)))
                self.end_headers()
                self.wfile.write(page_html)
                return
            if path == "/trainer.js":
                self.send_response(200)
                self.send_header("Content-Type", "text/javascript; charset=utf-8")
                self.send_header("Content-Length", str(len(trainer_js)))
                self.end_headers()
                self.wfile.write(trainer_js)
                return
            if path == "/hive/health":
                state.record_hive("GET", path)
                _json_bytes(self, 200, {"ok": True})
                return
            if path.startswith("/hive/api/work"):
                state.record_hive("GET", path)
                _json_bytes(self, 200, {"work": []})
                return
            if path == "/api/infinite-craft/pair":
                qs = parse_qs(parsed.query)
                first = (qs.get("first") or [""])[0]
                second = (qs.get("second") or [""])[0]
                state.record_pair(first, second)
                _json_bytes(self, 200, state.pair_body)
                return
            self.send_error(404)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            body = _read_json(self)
            state.record_hive("POST", path, body)
            if path == "/hive/api/lookup":
                pairs = []
                if isinstance(body, dict):
                    pairs = body.get("pairs") or []
                results = {}
                for p in pairs:
                    if not isinstance(p, list) or len(p) < 2:
                        continue
                    key = _pair_key(str(p[0]), str(p[1]))
                    if key in state.hive_hits:
                        results[key] = state.hive_hits[key]
                _json_bytes(self, 200, {"results": results})
                return
            if path == "/hive/api/beat":
                _json_bytes(self, 200, {"ok": True, "work": False})
                return
            if path == "/hive/api/contribute":
                _json_bytes(self, 200, {"added": 0, "dupes": 0, "total": 0})
                return
            self.send_error(404)

    return Handler


def _start_server(state: LabState) -> tuple[ThreadingHTTPServer, str]:
    page_html = _resolve_data_file("tests/chrome/page.html").read_bytes()
    trainer_js = _resolve_data_file("bookmarklet/trainer.js").read_bytes()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(state, page_html, trainer_js))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}/"


def _wait_until(pred, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.05)
    raise TimeoutError(what)


def _trainer_output(page) -> str:
    return page.locator("#ict-output").inner_text()


def _trainer_result_line(output: str) -> str:
    lines = [ln.strip() for ln in output.splitlines() if " = " in ln and " + " in ln]
    if not lines:
        raise AssertionError(f"no trainer combine line in output:\n{output}")
    return lines[-1]


def _boot(page, state: LabState, origin: str) -> None:
    page.goto(origin, wait_until="domcontentloaded")
    page.wait_for_selector("#ict-container", timeout=15_000)
    page.wait_for_function(
        """() => {
          const t = document.getElementById("ict-output")?.textContent || "";
          return t.includes("elements loaded");
        }""",
        timeout=15_000,
    )
    _wait_until(
        lambda: "/hive/health" in state.hive_paths(),
        10.0,
        "hive /health never requested",
    )
    # Warmup sets relayReachable from that health response; give the
    # promise a tick so apiPair will consult the hive.
    page.wait_for_timeout(200)


def _run_both_combines(page, state: LabState, origin: str) -> dict:
    _boot(page, state, origin)

    before = state.pair_count()
    page.fill("#ict-input", COMBINE_LINE)
    page.press("#ict-input", "Enter")
    page.wait_for_function(
        """() => {
          const t = document.getElementById("ict-output")?.textContent || "";
          return / = /.test(t) && t.includes("Cowboy");
        }""",
        timeout=15_000,
    )
    trainer_pairs = state.pair_count() - before
    trainer_output = _trainer_output(page)
    trainer_line = _trainer_result_line(trainer_output)

    page.evaluate("() => { window.__gameLast = null; }")
    page.click("#game-combine")
    page.wait_for_function("() => window.__gameLast !== null", timeout=10_000)
    game_json = page.evaluate("() => window.__gameLast")
    return {
        "trainer_output": trainer_output,
        "trainer_line": trainer_line,
        "trainer_pair_requests": trainer_pairs,
        "game_json": game_json,
        "game_result": (game_json or {}).get("result"),
    }


@pytest.fixture(scope="module")
def playwright_chromium():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
        except Exception as exc:
            pytest.skip(f"Playwright Chromium unavailable: {exc}")
        try:
            yield browser
        finally:
            browser.close()


def test_trainer_matches_game_when_hive_misses(playwright_chromium):
    """Empty hive: both surfaces spend a neal pair slot and see the product."""
    state = LabState()
    server, origin = _start_server(state)
    try:
        page = playwright_chromium.new_page()
        try:
            result = _run_both_combines(page, state, origin)
        finally:
            page.close()
    finally:
        server.shutdown()

    assert result["game_result"] == PRODUCT, result
    assert PRODUCT in result["trainer_line"], result
    assert result["trainer_pair_requests"] >= 1, result


def test_hive_nothing_is_a_hit_like_a_product(playwright_chromium):
    """A valid hive Nothing is served on first write, same as a product.

    The game button always hits /pair; the overlay uses the hive hit.
    """
    state = LabState(
        hive_hits={_pair_key(FIRST, SECOND): {"r": None, "e": ""}},
    )
    server, origin = _start_server(state)
    try:
        page = playwright_chromium.new_page()
        try:
            result = _run_both_combines(page, state, origin)
        finally:
            page.close()
    finally:
        server.shutdown()

    assert result["game_result"] == PRODUCT, result
    assert "Nothing" in result["trainer_line"], result
    assert PRODUCT not in result["trainer_line"], result
    assert result["trainer_pair_requests"] == 0, result


def test_hive_payload_without_result_is_a_miss(playwright_chromium):
    """A hive hit missing `r` is not Nothing — fall through to neal."""
    state = LabState(hive_hits={_pair_key(FIRST, SECOND): {}})
    server, origin = _start_server(state)
    try:
        page = playwright_chromium.new_page()
        try:
            result = _run_both_combines(page, state, origin)
        finally:
            page.close()
    finally:
        server.shutdown()

    assert result["game_result"] == PRODUCT, result
    assert PRODUCT in result["trainer_line"], result
    assert result["trainer_pair_requests"] >= 1, result


def _eq_count(page) -> int:
    return page.evaluate(
        """() => (document.getElementById("ict-output")?.textContent || "").split(" = ").length - 1"""
    )


def _trainer_combine(page, state: LabState) -> tuple[str, int]:
    before = state.pair_count()
    want = _eq_count(page) + 1
    page.fill("#ict-input", COMBINE_LINE)
    page.press("#ict-input", "Enter")
    page.wait_for_function(
        f"""() => {{
          const t = document.getElementById("ict-output")?.textContent || "";
          return t.split(" = ").length - 1 >= {int(want)};
        }}""",
        timeout=15_000,
    )
    line = _trainer_result_line(_trainer_output(page))
    return line, state.pair_count() - before


def test_valid_nothing_stays_in_pair_cache(playwright_chromium):
    """A valid Nothing is cached like a product — second combine skips neal."""
    state = LabState(
        hive_hits={_pair_key(FIRST, SECOND): {"r": None, "e": ""}},
    )
    server, origin = _start_server(state)
    try:
        page = playwright_chromium.new_page()
        try:
            _boot(page, state, origin)
            first_line, first_pairs = _trainer_combine(page, state)
            state.hive_hits.clear()
            second_line, second_pairs = _trainer_combine(page, state)
        finally:
            page.close()
    finally:
        server.shutdown()

    assert "Nothing" in first_line, first_line
    assert first_pairs == 0, first_pairs
    assert "Nothing" in second_line, second_line
    assert second_pairs == 0, second_pairs


@pytest.mark.parametrize(
    "body",
    [{}, {"result": None}, {"result": ""}],
)
def test_invalid_pair_body_is_not_nothing(playwright_chromium, body):
    """Missing/empty/non-string result is an error, never cached as Nothing."""
    state = LabState(pair_body=body)
    server, origin = _start_server(state)
    try:
        page = playwright_chromium.new_page()
        try:
            _boot(page, state, origin)
            page.fill("#ict-input", COMBINE_LINE)
            page.press("#ict-input", "Enter")
            page.wait_for_function(
                """() => {
                  const t = document.getElementById("ict-output")?.textContent || "";
                  return t.includes("Script aborted") || t.includes("API request failed");
                }""",
                timeout=20_000,
            )
            output = _trainer_output(page)
        finally:
            page.close()
    finally:
        server.shutdown()

    assert "API request failed" in output or "Script aborted" in output, output
    assert " = Nothing" not in output, output
    contribs = [
        r
        for r in state.hive_requests
        if r["path"] == "/hive/api/contribute" and r["body"]
    ]
    for rec in contribs:
        entries = rec["body"].get("entries") if isinstance(rec["body"], dict) else None
        if not entries:
            continue
        for entry in entries:
            if isinstance(entry, list) and len(entry) >= 3:
                assert entry[2] is not None, rec
