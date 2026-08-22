"""Hive-mind relay client — the shared pair-result cache tier.

Sync HTTP helpers (stdlib urllib; our own relay needs no Cloudflare
impersonation) meant to be called via asyncio.to_thread. Every call fails
open: a relay that is down, cold-starting, or unreachable must never break
or slow a run beyond the short timeouts here — the caller just skips the
tier and talks to neal.fun as before.
"""

import json
import os
import secrets
import urllib.request

DEFAULT_RELAY_URL = "https://infinite-craft-relay.onrender.com"

# Session identity. Every request carries a stable random session id; all
# liveness/state travels in the ~1s beat (see beat below), never in headers.
SESSION_ID = secrets.token_hex(8)

# The hive envelope from the most recent response: how many sessions on our
# public IP are spending neal budget, and whether the IP is cooling down.
last_hive: dict = {"peers": 0, "cooledUntil": 0}


def _headers() -> dict:
    return {"Content-Type": "application/json", "x-ic-session": SESSION_ID}


def _absorb_hive(data: dict) -> None:
    hive = data.get("hive")
    if isinstance(hive, dict):
        last_hive["peers"] = int(hive.get("peers") or 0)
        last_hive["cooledUntil"] = int(hive.get("cooledUntil") or 0)

# The health ping doubles as a wake-up call for a spun-down free instance,
# so it tolerates more latency than the in-pipeline lookups.
PING_TIMEOUT = 8.0
LOOKUP_TIMEOUT = 4.0
CONTRIBUTE_TIMEOUT = 8.0
BEAT_TIMEOUT = 2.5  # beats are ~1s apart; a slow beat just spaces the next
LOOKUP_BATCH = 5000
CONTRIBUTE_BATCH = 2000


def relay_url() -> str:
    return os.environ.get("IC_RELAY_URL", DEFAULT_RELAY_URL).rstrip("/")


def _post_json(path: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        relay_url() + path,
        data=json.dumps(payload).encode("utf-8"),
        headers=_headers(),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        _absorb_hive(data)
        return data


def _get_json(path: str, timeout: float) -> dict:
    req = urllib.request.Request(relay_url() + path, headers=_headers())
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        _absorb_hive(data)
        return data


def ping() -> dict | None:
    """GET /health. Returns the health dict, or None if unreachable."""
    try:
        req = urllib.request.Request(relay_url() + "/health", headers=_headers())
        with urllib.request.urlopen(req, timeout=PING_TIMEOUT) as resp:
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


def beat(neal_ok: bool, run_id: str = "", cooled_until_ms: int = 0) -> tuple[bool, bool] | None:
    """THE one timer (~1s): liveness out, a work bit back.

    Everything the relay needs to know rides here — neal reachability, the
    active run id (bounties live exactly as long as their run keeps
    appearing), and any 429 cooldown to broadcast. Returns (ok, work), or
    None when the relay is unreachable (the next successful beat is the
    recovery — there is no separate probe)."""
    try:
        body: dict = {"nealOk": bool(neal_ok)}
        if run_id:
            body["runId"] = run_id
        if cooled_until_ms:
            body["cooledUntil"] = int(cooled_until_ms)
        data = _post_json("/api/beat", body, BEAT_TIMEOUT)
        return bool(data.get("ok")), bool(data.get("work"))
    except Exception:
        return None


def pull_work(limit: int = 5) -> list[dict] | None:
    """Pull up to `limit` assigned work items ({kind, first, second}) —
    called when the beat's work bit says there is some. [] when refused
    or empty; None when unreachable."""
    try:
        data = _get_json(f"/api/work?limit={int(limit)}", LOOKUP_TIMEOUT)
        return list(data.get("work") or [])
    except Exception:
        return None


def sync_bounties(pairs: list[tuple[str, str]], run_id: str) -> dict | None:
    """Offer overflow pairs to the hive, bound to the caller's live run.

    Pairs the hive already knows come back in `results` (same shape as
    lookup) instead of posting; the rest live on the board exactly as long
    as `run_id` keeps appearing in our beats. Returns {"results",
    "posted"}, or None when unreachable."""
    try:
        data = _post_json(
            "/api/bounties",
            {"pairs": [[a, b] for a, b in pairs[:500]], "runId": run_id},
            CONTRIBUTE_TIMEOUT,
        )
        out = {}
        for key, v in (data.get("results") or {}).items():
            out[key] = (v.get("r"), v.get("e") or "")
        return {"results": out, "posted": int(data.get("posted") or 0)}
    except Exception:
        return None
