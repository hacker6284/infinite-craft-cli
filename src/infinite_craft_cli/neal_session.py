"""Keep a live Neal Infinite Craft browser session for pair credentials.

Pair requests need Cloudflare Turnstile (`token` query) and
`x-craft-session`. Those are minted on https://neal.fun/infinite-craft/.
This module opens a real Chrome window, waits until the page has both
values, and re-reads them (reloading if they disappear) for every pair.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import Any

GAME_URL = "https://neal.fun/infinite-craft/"
_READ_CREDS_JS = """() => ({
  token: window.turnstileToken || "",
  session: window.craftSessionToken || "",
  title: document.title || "",
})"""


class NealSessionRequired(Exception):
    """No Turnstile token / craft session — the pair hop must not be sent."""


class NealSession:
    """One headed Chrome on infinite-craft, held for the CLI process."""

    def __init__(
        self,
        profile_dir: str,
        *,
        status: Callable[[str], None] | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        self._profile_dir = profile_dir
        self._status = status or (lambda _msg: None)
        self._timeout_s = timeout_s
        self._pw: Any = None
        self._context: Any = None
        self._page: Any = None

    async def credentials(self) -> tuple[str, str]:
        """Return live (turnstile token, craft session). Mint or refresh if needed."""
        if self._page is not None:
            creds = await self._read()
            if creds is not None:
                return creds
            self._status("Neal session expired — refreshing.")
            await self._reload_and_wait()
            creds = await self._read()
            if creds is not None:
                return creds
        await self._launch_and_wait()
        creds = await self._read()
        if creds is None:
            raise NealSessionRequired(
                "Could not get a Neal session. If a verification checkbox "
                "appeared, click it, then try again."
            )
        return creds

    async def close(self) -> None:
        page, context, pw = self._page, self._context, self._pw
        self._page = None
        self._context = None
        self._pw = None
        for obj, method in ((page, "close"), (context, "close"), (pw, "stop")):
            if obj is None:
                continue
            with _suppress():
                await getattr(obj, method)()

    async def _read(self) -> tuple[str, str] | None:
        if self._page is None:
            return None
        try:
            data = await self._page.evaluate(_READ_CREDS_JS)
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        token = data.get("token") or ""
        session = data.get("session") or ""
        if token and session:
            return (str(token), str(session))
        return None

    async def _launch_and_wait(self) -> None:
        self._status("Opening Infinite Craft for a Neal session.")
        self._status("If a verification checkbox appears, click it.")
        os.makedirs(self._profile_dir, exist_ok=True)
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:
            raise NealSessionRequired(
                "Playwright is required for a Neal session (pip install playwright)."
            ) from e
        if self._pw is None:
            self._pw = await async_playwright().start()
        if self._context is None:
            self._context = await self._open_context()
        pages = list(self._context.pages or [])
        self._page = pages[0] if pages else await self._context.new_page()
        try:
            await self._page.goto(GAME_URL, wait_until="domcontentloaded", timeout=90_000)
        except Exception as e:
            raise NealSessionRequired(
                f"Could not open Infinite Craft in Chrome ({e})."
            ) from e
        await self._wait_for_tokens(announce=True)

    async def _reload_and_wait(self) -> None:
        if self._page is None:
            await self._launch_and_wait()
            return
        try:
            await self._page.reload(wait_until="domcontentloaded", timeout=90_000)
        except Exception:
            await self._launch_and_wait()
            return
        await self._wait_for_tokens(announce=False)

    async def _wait_for_tokens(self, *, announce: bool) -> None:
        deadline = asyncio.get_running_loop().time() + self._timeout_s
        while asyncio.get_running_loop().time() < deadline:
            creds = await self._read()
            if creds is not None:
                if announce:
                    self._status("Neal session ready.")
                return
            await asyncio.sleep(0.4)
        raise NealSessionRequired(
            "Timed out waiting for a Neal session. If a verification "
            "checkbox appeared, click it, then try again."
        )

    async def _open_context(self) -> Any:
        chromium = self._pw.chromium
        args = [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        last_err: Exception | None = None
        for kwargs in (
            {"channel": "chrome"},
            {"channel": "chromium"},
            {},
        ):
            try:
                return await chromium.launch_persistent_context(
                    self._profile_dir,
                    headless=False,
                    viewport={"width": 1100, "height": 800},
                    args=args,
                    **kwargs,
                )
            except Exception as e:
                last_err = e
                continue
        raise NealSessionRequired(
            "Could not open Chrome for a Neal session. Install Google Chrome, "
            "or run: python3 -m playwright install chromium"
        ) from last_err


class _suppress:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *args: object) -> bool:
        return True
