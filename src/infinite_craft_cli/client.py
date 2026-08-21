"""HTTP clients for Infinite Craft and Infinibrowser APIs."""

import time

from infinite_craft_cli.element import Element
from typing import Callable

from infinite_craft_cli._sudo import craft
from infinite_craft_cli.ratelimit import RateLimiter, RateLimitCancelled, RateLimitToken


class NealRateLimited(Exception):
    """neal.fun returned 429 — an hours-long IP ban, not a backoff signal.

    Callers must stand down (cooldown) rather than retry; retrying while
    banned risks extending the ban."""

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


def ib_get(url: str, params: dict | None = None, timeout: float | None = None):
    """Sync GET with the kernel's shared Infinibrowser retry policy.

    Retries rate limiting (429) and transport failures with the kernel's
    backoff schedule — the same policy the browser trainer applies — and
    returns any other response as-is for the caller to interpret. Returns
    None once transport-failure retries are exhausted.
    """
    if timeout is None:
        timeout = craft.fetch_timeout_ms() / 1000.0
    attempt = 0
    while True:
        resp = None
        status = 0  # kernel convention: 0 = transport failure
        try:
            resp = _get_sync_session().get(url, params=params, timeout=timeout)
            status = resp.status_code
        except Exception:
            pass
        if resp is not None and status != 429:
            return resp
        if not craft.ib_should_retry(status, attempt):
            return resp  # final 429 response, or None on transport failure
        time.sleep(craft.ib_retry_backoff_ms(attempt) / 1000.0)
        attempt += 1


def fetch_json(
    url: str, params: dict | None = None, timeout: float | None = None, use_cache: bool = True
) -> dict | None:
    """Sync GET returning parsed JSON, with caching. Returns None on error.

    Error bodies are still parsed and returned (Infinibrowser reports
    misses as JSON with a "code" key); None means transport or parse
    failure. Uses curl_cffi with Chrome impersonation for Cloudflare bypass.
    """
    cache_key = f"{url}?{params}" if params else url
    if use_cache and cache_key in _sync_cache:
        return _sync_cache[cache_key]
    resp = ib_get(url, params=params, timeout=timeout)
    if resp is None:
        return None  # don't cache errors
    try:
        result = resp.json()
    except Exception:
        return None
    _sync_cache[cache_key] = result
    return result


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

    async def pair(self, first_name: str, second_name: str, *, fleet: bool = False) -> Element:
        """Combine two elements via the API. Returns the resulting Element.

        Returns Element(name=None) if the combination produces nothing.
        ``fleet=True`` tags the consumed rate slot as hive-bounty spend
        (rendered gold in the rate bar). Raises NealRateLimited on a 429 —
        neal's 429 is an hours-long IP ban, so callers must stand down,
        never retry.
        """
        token: RateLimitToken = await self._rate_limiter.acquire(
            cancel_check=self._cancel_check,
            sleep_step=self._rate_limit_sleep_step,
            _wait_callback=self._rate_limit_wait_callback,
        )
        if self._cancel_check and self._cancel_check():
            await self._rate_limiter.release(token)
            raise RateLimitCancelled()
        if fleet:
            self._rate_limiter.mark_fleet(token)

        resp = await self._session.get(
            _PAIR_ENDPOINT,
            params={"first": first_name, "second": second_name},
            allow_redirects=True,
            verify=True,
            impersonate=_IMPERSONATE,
            # Explicit: this matched curl_cffi's library default only by luck.
            timeout=craft.fetch_timeout_ms() / 1000.0,
        )
        if resp.status_code == 429:
            raise NealRateLimited()
        resp.raise_for_status()
        data = resp.json()

        if data.get("result") == "Nothing":
            return Element(name=None)

        return Element(
            name=data.get("result"),
            emoji=data.get("emoji"),
            is_first_discovery=data.get("isNew"),
        )
