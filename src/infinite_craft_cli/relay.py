"""Hive-mind relay client — the shared pair-result cache tier.

Sync HTTP helpers (stdlib urllib; our own relay needs no Cloudflare
impersonation) meant to be called via asyncio.to_thread. Every call fails
open: a relay that is down, cold-starting, or unreachable must never break
or slow a run beyond the short timeouts here — the caller just skips the
tier and talks to neal.fun as before.
"""

import json
import os
import urllib.request

DEFAULT_RELAY_URL = "https://infinite-craft-relay.onrender.com"

# The health ping doubles as a wake-up call for a spun-down free instance,
# so it tolerates more latency than the in-pipeline lookups.
PING_TIMEOUT = 8.0
LOOKUP_TIMEOUT = 4.0
CONTRIBUTE_TIMEOUT = 8.0
LOOKUP_BATCH = 5000
CONTRIBUTE_BATCH = 2000


def relay_url() -> str:
    return os.environ.get("IC_RELAY_URL", DEFAULT_RELAY_URL).rstrip("/")


def _post_json(path: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        relay_url() + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ping() -> dict | None:
    """GET /health. Returns the health dict, or None if unreachable."""
    try:
        with urllib.request.urlopen(relay_url() + "/health", timeout=PING_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data if isinstance(data, dict) and data.get("ok") else None
    except Exception:
        return None


def lookup(pairs: list[tuple[str, str]]) -> dict[str, tuple[str | None, str]] | None:
    """Batch lookup. pairs: [(first, second), ...] in any order.

    Returns {canonical NUL-joined key: (result_name | None-for-Nothing,
    emoji)} containing only hits, or None when the relay is unreachable.
    """
    out: dict[str, tuple[str | None, str]] = {}
    try:
        for i in range(0, len(pairs), LOOKUP_BATCH):
            chunk = [[a, b] for a, b in pairs[i : i + LOOKUP_BATCH]]
            data = _post_json("/api/lookup", {"pairs": chunk}, LOOKUP_TIMEOUT)
            for key, v in (data.get("results") or {}).items():
                out[key] = (v.get("r"), v.get("e") or "")
    except Exception:
        return None
    return out


def contribute(entries: list[tuple[str, str, str | None, str]]) -> int | None:
    """Contribute results. entries: [(first, second, result|None, emoji), ...].

    Returns the number of entries the relay hadn't seen, or None when
    unreachable.
    """
    added = 0
    try:
        for i in range(0, len(entries), CONTRIBUTE_BATCH):
            chunk = [[a, b, r, e] for a, b, r, e in entries[i : i + CONTRIBUTE_BATCH]]
            data = _post_json("/api/contribute", {"entries": chunk}, CONTRIBUTE_TIMEOUT)
            added += int(data.get("added") or 0)
    except Exception:
        return None
    return added


def stats() -> dict | None:
    """GET /api/stats, or None if unreachable."""
    try:
        with urllib.request.urlopen(relay_url() + "/api/stats", timeout=PING_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
