"""HTTP clients for Infinite Craft and Infinibrowser APIs."""

from infinite_craft_cli.element import Element
from typing import Callable

from infinite_craft_cli.ratelimit import RateLimiter, RateLimitCancelled, RateLimitToken

_BASE_URL = "https://neal.fun"
_PAIR_ENDPOINT = "/api/infinite-craft/pair"
_IMPERSONATE = "chrome120"

_HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "priority": "u=1, i",
    "cache-control": "no-cache",
    "pragma": "no-cache",
    "sec-ch-ua": '"Not_A Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "Origin": _BASE_URL,
    "Referer": f"{_BASE_URL}/infinite-craft/",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/127.0.0.0 Safari/537.36"
    ),
}


# ---------------------------------------------------------------------------
# Sync HTTP helper (used for Infinibrowser)
# ---------------------------------------------------------------------------
_sync_session = None
_sync_cache: dict[str, dict | None] = {}


def _get_sync_session():
    """Lazy-init a sync curl_cffi session with Chrome impersonation."""
    global _sync_session
    if _sync_session is None:
        from curl_cffi.requests import Session

        _sync_session = Session(impersonate=_IMPERSONATE)
    return _sync_session


def fetch_json(
    url: str, params: dict | None = None, timeout: int = 15, use_cache: bool = True
) -> dict | None:
    """Sync GET returning parsed JSON, with caching. Returns None on error.

    Uses curl_cffi with Chrome impersonation for Cloudflare bypass.
    """
    cache_key = f"{url}?{params}" if params else url
    if use_cache and cache_key in _sync_cache:
        return _sync_cache[cache_key]
    try:
        resp = _get_sync_session().get(url, params=params, timeout=timeout)
        result = resp.json()
    except Exception:
        return None  # don't cache errors
    _sync_cache[cache_key] = result
    return result


def clear_fetch_cache():
    """Clear the sync fetch cache (useful for testing)."""
    _sync_cache.clear()


class InfiniteCraftClient:
    """Async client for the neal.fun Infinite Craft pairing API."""

    def __init__(
        self,
        rate_limit: int = 60,
        cancel_check: Callable[[], bool] | None = None,
        *,
        rate_limit_sleep_step: float = 0.1,
        _rate_limit_wait_callback: Callable[[bool], None] | None = None,
    ):
        self._rate_limiter = RateLimiter(max_requests=rate_limit)
        self._cancel_check = cancel_check
        self._rate_limit_sleep_step = rate_limit_sleep_step
        self._rate_limit_wait_callback = _rate_limit_wait_callback
        self._session = None

    async def __aenter__(self):
        from curl_cffi.requests import AsyncSession

        self._session = AsyncSession(
            base_url=_BASE_URL,
            headers=_HEADERS,
        )
        # Visit the site first to acquire Cloudflare cookies
        await self._session.get(
            url=_BASE_URL,
            allow_redirects=True,
            verify=True,
            impersonate=_IMPERSONATE,
        )
        return self

    async def __aexit__(self, *args):
        if self._session:
            await self._session.close()
            self._session = None

    async def pair(self, first_name: str, second_name: str) -> Element:
        """Combine two elements via the API. Returns the resulting Element.

        Returns Element(name=None) if the combination produces nothing.
        """
        token: RateLimitToken = await self._rate_limiter.acquire(
            cancel_check=self._cancel_check,
            sleep_step=self._rate_limit_sleep_step,
            _wait_callback=self._rate_limit_wait_callback,
        )
        if self._cancel_check and self._cancel_check():
            await self._rate_limiter.release(token)
            raise RateLimitCancelled()

        resp = await self._session.get(
            _PAIR_ENDPOINT,
            params={"first": first_name, "second": second_name},
            allow_redirects=True,
            verify=True,
            impersonate=_IMPERSONATE,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("result") == "Nothing":
            return Element(name=None)

        return Element(
            name=data.get("result"),
            emoji=data.get("emoji"),
            is_first_discovery=data.get("isNew"),
        )
