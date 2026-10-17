"""Tests for NealSession mint/refresh (Playwright mocked)."""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

from infinite_craft_cli.neal_session import NealSession, NealSessionRequired


def run_async(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=5.0))


class FakePage:
    def __init__(self, token="tok", session="sess"):
        self.token = token
        self.session = session
        self.goto_calls = 0
        self.reload_calls = 0

    async def evaluate(self, _js):
        return {
            "token": self.token,
            "session": self.session,
            "title": "Infinite Craft",
        }

    async def goto(self, *_a, **_k):
        self.goto_calls += 1

    async def reload(self, **_k):
        self.reload_calls += 1
        if not self.token:
            self.token = "tok"
            self.session = "sess"

    async def close(self):
        return None


class FakeContext:
    def __init__(self, page):
        self.pages = [page]

    async def new_page(self):
        return self.pages[0]

    async def close(self):
        return None


def _patch_playwright(page):
    context = FakeContext(page)
    chromium = MagicMock()
    chromium.launch_persistent_context = AsyncMock(return_value=context)
    pw = MagicMock()
    pw.chromium = chromium
    pw.stop = AsyncMock()
    starter = MagicMock()
    starter.start = AsyncMock(return_value=pw)
    return starter, chromium, page


class TestNealSession:
    def test_credentials_from_open_page(self):
        page = FakePage()
        starter, chromium, _ = _patch_playwright(page)
        with patch("playwright.async_api.async_playwright", return_value=starter):
            session = NealSession("/tmp/ic-neal-test", timeout_s=2)
            token, sess = run_async(session.credentials())
            run_async(session.close())
        assert token == "tok"
        assert sess == "sess"
        assert chromium.launch_persistent_context.await_count == 1
        assert page.goto_calls == 1

    def test_reload_when_tokens_drop(self):
        page = FakePage(token="", session="")
        starter, _, _ = _patch_playwright(page)
        with patch("playwright.async_api.async_playwright", return_value=starter):
            session = NealSession("/tmp/ic-neal-test", timeout_s=2)
            session._pw = MagicMock()
            session._pw.stop = AsyncMock()
            session._context = FakeContext(page)
            session._page = page
            token, sess = run_async(session.credentials())
            run_async(session.close())
        assert token == "tok"
        assert sess == "sess"
        assert page.reload_calls == 1

    def test_timeout_without_tokens(self):
        page = FakePage(token="", session="")
        page.reload = AsyncMock()
        starter, chromium, _ = _patch_playwright(page)
        chromium.launch_persistent_context = AsyncMock(return_value=FakeContext(page))
        with patch("playwright.async_api.async_playwright", return_value=starter):
            session = NealSession("/tmp/ic-neal-test", timeout_s=0.5)
            with pytest.raises(NealSessionRequired, match="Timed out"):
                run_async(session.credentials())
            run_async(session.close())
