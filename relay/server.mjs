// Infinite Craft hive-mind relay — shared pair-result cache.
//
// One small Render web service that every CLI/trainer instance can consult
// before spending a neal.fun rate-limit slot. The durable store is the union
// of everyone's local caches: clients re-seed the relay from their own recipe
// stores on connect, so a cold instance (free tier: ephemeral disk, spin-down
// on idle) refills within minutes. An optional Upstash Redis snapshot
// (UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN) survives restarts; when
// the env vars are absent the relay runs RAM + re-seed only.
//
// Zero runtime dependencies — node:http only, Upstash via fetch.
//
// API (all JSON, CORS *):
//   GET  /health          → { ok, entries }
//   GET  /api/stats       → { entries, uptimeSec, snapshot }
//   POST /api/lookup      → { pairs: [[first, second], ...] }
//                           → { results: { "<a>\0<b>": { r, e } } }
//                             (key is the canonical NUL-joined pair; misses
//                              are simply absent from the map)
//   POST /api/contribute  → { entries: [[first, second, result|null, emoji], ...] }
//                           → { added, dupes, total }
//                             (result null = "Nothing"; first write wins —
//                              entries are never overwritten, only confirmed)
//
// Entry values are { r: result|null, e: emoji, c: confirms }. `c` counts
// independent contributions of the same (pair, result) — the raw material
// for peer review (bounty board, phase 3).

import http from "node:http";
// The shared sudo kernel (generated JS backend, vendored under relay/_sudo
// because Render deploys straight from git with no build step; a CI
// freshness test diffs it against the Bazel output). Canonicalization and
// sanitization MUST come from here — hand-rolled mirrors are exactly the
// divergence class that bit us in stress finding F2.
import {
  pair_key as pairKeyKernel,
  sanitize_element_name as sanitizeKernel,
} from "./_sudo/craft.mjs";

const PORT = Number(process.env.PORT || 8790);
const MAX_ENTRIES = Number(process.env.RELAY_MAX_ENTRIES || 1_500_000);
const MAX_LOOKUP = 20_000; // pairs per /api/lookup call
const MAX_CONTRIB = 5_000; // entries per /api/contribute call
const MAX_BODY = 8 * 1024 * 1024;
const MAX_NAME = 200;
const MAX_EMOJI = 16;
const SNAPSHOT_INTERVAL_MS = Number(process.env.RELAY_SNAPSHOT_MS || 60_000);

// Upstash's dashboard copy-paste wraps values in quotes (KEY="value");
// strip them so pasted-as-is env vars just work.
export function envStr(raw) {
  let v = (raw || "").trim();
  if (
    (v.startsWith('"') && v.endsWith('"') && v.length >= 2) ||
    (v.startsWith("'") && v.endsWith("'") && v.length >= 2)
  ) {
    v = v.slice(1, -1);
  }
  return v;
}

const UPSTASH_URL = envStr(process.env.UPSTASH_REDIS_REST_URL).replace(/\/+$/, "");
const UPSTASH_TOKEN = envStr(process.env.UPSTASH_REDIS_REST_TOKEN);
const SNAP_KEY = "pairs";

/** key "<a>\0<b>" → { r: string|null, e: string, c: number, rv: boolean }
    c = independent identical claims; rv = peer-reviewed (c >= 2). */
const entries = new Map();
/** keys written since the last successful snapshot flush */
const dirty = new Set();
/** keys deleted since the last flush (mismatch healing) — need HDEL */
const deletedDirty = new Set();

// ── presence: who is on each public IP, and in what state ────────────
// ip → Map<session, {state, at}>. Fed by x-ic-session/x-ic-state headers on
// every API call; entries expire after PRESENCE_TTL. "running"/"serving"
// count as spending the IP's neal budget; the count is returned to every
// caller so clients split the per-IP window without talking to each other.
const PRESENCE_TTL_MS = 180_000;
const presence = new Map();
// ip → epoch-ms until which the IP is cooling down (one session tripped a
// neal 429 — an IP-scoped, hours-long ban). Broadcast to all sessions.
const ipCooldown = new Map();

// ── bounty board ─────────────────────────────────────────────────────
// key → {first, second, at, claimedBy, claimedAt}. Posted by rate-limited
// clients; offered only to callers whose IP is fully idle and not cooling.
const bounties = new Map();
const MAX_BOUNTIES = 10_000;
const BOUNTY_TTL_MS = 15 * 60_000;
const CLAIM_TTL_MS = 90_000;
const MAX_POST = 500; // bounties per POST
const REVIEW_PENDING_MAX = 100_000; // review backlog memory bound
// Unreviewed entries awaiting a peer re-ask of neal (c == 1). Offered to
// idle clients when no pair bounties are open. key → claim {by, at} | null.
const reviewPending = new Map();

function callerIp(req) {
  const fwd = req.headers["x-forwarded-for"];
  if (typeof fwd === "string" && fwd.length) return fwd.split(",")[0].trim();
  return req.socket.remoteAddress || "?";
}

function touchPresence(req, now) {
  const session = envStr(req.headers["x-ic-session"] || "");
  if (!session) return;
  const state = envStr(req.headers["x-ic-state"] || "idle");
  const ip = callerIp(req);
  let bySession = presence.get(ip);
  if (!bySession) {
    bySession = new Map();
    presence.set(ip, bySession);
  }
  bySession.set(session.slice(0, 64), { state, at: now });
  if (state === "cooled") {
    const until = Number(envStr(req.headers["x-ic-cooled-until"] || "")) || 0;
    const prev = ipCooldown.get(ip) || 0;
    if (until > prev) ipCooldown.set(ip, until);
  }
}

function ipSnapshot(req, now) {
  const ip = callerIp(req);
  const bySession = presence.get(ip);
  let spending = 0;
  let running = 0;
  if (bySession) {
    for (const [session, info] of bySession) {
      if (now - info.at > PRESENCE_TTL_MS) {
        bySession.delete(session);
        continue;
      }
      if (info.state === "running" || info.state === "serving") spending++;
      if (info.state === "running") running++;
    }
    if (bySession.size === 0) presence.delete(ip);
  }
  let cooledUntil = ipCooldown.get(ip) || 0;
  if (cooledUntil <= now) {
    ipCooldown.delete(ip);
    cooledUntil = 0;
  }
  return { spending, running, cooledUntil };
}

/** The hive envelope attached to every API response: what the caller's IP
    looks like right now, so clients can split budget and stand down. */
function hiveEnvelope(req, now) {
  const snap = ipSnapshot(req, now);
  return { peers: snap.spending, cooledUntil: snap.cooledUntil };
}

function pruneBounties(now) {
  for (const [key, b] of bounties) {
    if (now - b.at > BOUNTY_TTL_MS) bounties.delete(key);
  }
}
const bootAt = Date.now();
const snapshot = {
  enabled: Boolean(UPSTASH_URL && UPSTASH_TOKEN),
  loaded: 0,
  lastOkAt: null,
  lastErr: null,
};

// ── canonicalization: kernel-owned ───────────────────────────────────
export function pairKey(a, b) {
  const [ka, kb] = pairKeyKernel(a, b);
  return ka + "\0" + kb;
}

// Kernel storage normalization plus a relay-only DoS length bound,
// measured in CODE POINTS so astral-heavy names get the same budget as
// ASCII — real game names are far shorter than 200 either way.
export function sanitizeName(raw) {
  if (typeof raw !== "string") return "";
  const out = sanitizeKernel(raw);
  let cps = 0;
  for (const _ch of out) {
    cps += 1;
    if (cps > MAX_NAME) return "";
  }
  return out;
}

function sanitizeEmoji(raw) {
  if (typeof raw !== "string") return "";
  const e = raw.trim();
  return e.length > MAX_EMOJI ? "" : e;
}

// ── core ops (exported for tests) ────────────────────────────────────
export function doLookup(pairs) {
  const results = {};
  let n = 0;
  for (const p of pairs) {
    if (n++ >= MAX_LOOKUP) break;
    if (!Array.isArray(p) || p.length < 2) continue;
    const a = sanitizeName(p[0]);
    const b = sanitizeName(p[1]);
    if (!a || !b) continue;
    const key = pairKey(a, b);
    const hit = entries.get(key);
    if (hit) results[key] = { r: hit.r, e: hit.e };
  }
  return { results };
}

export function doContribute(list) {
  let added = 0;
  let dupes = 0;
  let n = 0;
  for (const item of list) {
    if (n++ >= MAX_CONTRIB) break;
    if (!Array.isArray(item) || item.length < 3) continue;
    const a = sanitizeName(item[0]);
    const b = sanitizeName(item[1]);
    if (!a || !b) continue;
    const r = item[2] === null ? null : sanitizeName(item[2]);
    if (r === "") continue; // non-null but sanitized away → junk
    const e = sanitizeEmoji(item.length > 3 ? item[3] : "");
    const key = pairKey(a, b);
    const prev = entries.get(key);
    if (prev) {
      if (prev.r === r) {
        // Independent identical claim = a peer review passing: one
        // confirmation (per design review) flips the entry to reviewed.
        prev.c += 1;
        if (prev.c >= 2 && !prev.rv) {
          prev.rv = true;
          reviewPending.delete(key);
        }
        dirty.add(key);
      } else if (!prev.rv) {
        // Conflicting claim against an UNREVIEWED entry: neal is
        // deterministic, so one of the two claims is wrong — drop the
        // entry and re-open the pair as a bounty so the network
        // re-derives it fresh (self-healing beats bookkeeping).
        entries.delete(key);
        dirty.delete(key);
        deletedDirty.add(key);
        reviewPending.delete(key);
        if (bounties.size < MAX_BOUNTIES && !bounties.has(key)) {
          bounties.set(key, { first: a, second: b, at: Date.now(), claimedBy: "", claimedAt: 0 });
        }
      }
      // Conflicts against a reviewed entry are ignored: 2+ independent
      // neal sightings beat a lone dissenter.
      dupes++;
      continue;
    }
    // At capacity: skip the insert but keep scanning — confirmations for
    // already-present keys later in the batch must still land.
    if (entries.size >= MAX_ENTRIES) continue;
    entries.set(key, { r, e, c: 1, rv: false });
    dirty.add(key);
    if (reviewPending.size < REVIEW_PENDING_MAX) reviewPending.set(key, null);
    bounties.delete(key); // a fresh result fulfills any open bounty
    added++;
  }
  return { added, dupes, total: entries.size };
}

// ── bounty ops ───────────────────────────────────────────────────────
export function doPostBounties(pairs, now) {
  // Pairs already cached come straight back as results (no bounty needed);
  // the rest go on the board.
  const results = {};
  let posted = 0;
  let m = 0;
  for (const p of pairs) {
    if (m++ >= MAX_POST) break;
    if (!Array.isArray(p) || p.length < 2) continue;
    const a = sanitizeName(p[0]);
    const b = sanitizeName(p[1]);
    if (!a || !b) continue;
    const key = pairKey(a, b);
    const hit = entries.get(key);
    if (hit) {
      results[key] = { r: hit.r, e: hit.e };
      continue;
    }
    if (bounties.has(key) || bounties.size >= MAX_BOUNTIES) continue;
    bounties.set(key, { first: a, second: b, at: now, claimedBy: "", claimedAt: 0 });
    posted++;
  }
  return { results, posted, open: bounties.size };
}

export function doTakeBounties(limit, session, snap, now) {
  // Eligibility: the caller's whole IP must be idle (no running session)
  // and not cooling down — bounty work must never contest a household's
  // own runs or a banned IP.
  if (snap.cooledUntil > now) return { bounties: [], reason: "cooled" };
  if (snap.running > 0) return { bounties: [], reason: "ip-active" };
  pruneBounties(now);
  const out = [];
  const cap = Math.max(1, Math.min(limit || 5, 20));
  for (const [key, b] of bounties) {
    if (out.length >= cap) break;
    if (b.claimedBy && now - b.claimedAt <= CLAIM_TTL_MS) continue;
    b.claimedBy = session || "?";
    b.claimedAt = now;
    out.push({ kind: "pair", first: b.first, second: b.second });
  }
  // Idle capacity beyond open bounties goes to peer review: re-ask neal
  // about unreviewed entries. The expected answer is never shared — the
  // reviewer's independent contribute confirms or heals the entry.
  if (out.length < cap) {
    for (const [key, claim] of reviewPending) {
      if (out.length >= cap) break;
      const entry = entries.get(key);
      if (!entry || entry.rv) {
        reviewPending.delete(key);
        continue;
      }
      if (claim && now - claim.at <= CLAIM_TTL_MS) continue;
      const i = key.indexOf("\0");
      if (i < 0) continue;
      reviewPending.set(key, { by: session || "?", at: now });
      out.push({ kind: "review", first: key.slice(0, i), second: key.slice(i + 1) });
    }
  }
  return { bounties: out };
}

// ── Upstash snapshot (optional backstop) ─────────────────────────────
async function upstash(commands) {
  const resp = await fetch(`${UPSTASH_URL}/pipeline`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${UPSTASH_TOKEN}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(commands),
  });
  if (!resp.ok) throw new Error(`upstash ${resp.status}`);
  return resp.json();
}

async function snapshotLoad() {
  if (!snapshot.enabled) return;
  let cursor = "0";
  do {
    const [res] = await upstash([["HSCAN", SNAP_KEY, cursor, "COUNT", "1000"]]);
    const [next, flat] = res.result;
    cursor = String(next);
    for (let i = 0; i + 1 < flat.length; i += 2) {
      try {
        const v = JSON.parse(flat[i + 1]);
        if (!entries.has(flat[i]) && entries.size < MAX_ENTRIES) {
          const c = v.c || 1;
          const rv = !!v.rv || c >= 2;
          entries.set(flat[i], { r: v.r ?? null, e: v.e || "", c, rv });
          if (!rv && reviewPending.size < REVIEW_PENDING_MAX) {
            reviewPending.set(flat[i], null);
          }
          snapshot.loaded++;
        }
      } catch {
        /* skip corrupt field */
      }
    }
  } while (cursor !== "0");
}

async function snapshotFlush() {
  if (!snapshot.enabled) return;
  if (deletedDirty.size) {
    const gone = [...deletedDirty];
    for (let i = 0; i < gone.length; i += 400) {
      await upstash([["HDEL", SNAP_KEY, ...gone.slice(i, i + 400)]]);
    }
    for (const k of gone) deletedDirty.delete(k);
  }
  if (dirty.size === 0) {
    snapshot.lastOkAt = Date.now();
    snapshot.lastErr = null;
    return;
  }
  const keys = [...dirty];
  const FIELDS_PER_HSET = 200;
  const HSETS_PER_CALL = 10;
  for (let i = 0; i < keys.length; i += FIELDS_PER_HSET * HSETS_PER_CALL) {
    const commands = [];
    for (let j = 0; j < HSETS_PER_CALL; j++) {
      const slice = keys.slice(
        i + j * FIELDS_PER_HSET,
        i + (j + 1) * FIELDS_PER_HSET
      );
      if (!slice.length) break;
      const cmd = ["HSET", SNAP_KEY];
      for (const k of slice) {
        const v = entries.get(k);
        if (v) cmd.push(k, JSON.stringify(v));
      }
      if (cmd.length > 2) commands.push(cmd);
    }
    if (commands.length) await upstash(commands);
  }
  for (const k of keys) dirty.delete(k);
  snapshot.lastOkAt = Date.now();
  snapshot.lastErr = null;
}

function startSnapshotLoop() {
  if (!snapshot.enabled) return;
  const tick = async () => {
    try {
      await snapshotFlush();
    } catch (e) {
      snapshot.lastErr = String(e && e.message ? e.message : e);
    }
    setTimeout(tick, SNAPSHOT_INTERVAL_MS).unref();
  };
  setTimeout(tick, SNAPSHOT_INTERVAL_MS).unref();
}

// ── http plumbing ────────────────────────────────────────────────────
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
  "Access-Control-Max-Age": "86400",
};

function send(res, status, body) {
  const data = JSON.stringify(body);
  res.writeHead(status, { "Content-Type": "application/json", ...CORS });
  res.end(data);
}

// Oversized uploads: destroying the socket mid-upload races the 413 and the
// client sees a bare connection reset (stress finding F1); responding early
// races the client's remaining writes the same way. So drain the body
// (discarding, bounded by MAX_DRAIN against abuse) and reject at `end` —
// the request is fully consumed, keep-alive stays sound, and the client
// reliably reads the 413.
const MAX_DRAIN = 64 * 1024 * 1024;

function readBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    let overflowed = false;
    const chunks = [];
    req.on("data", (c) => {
      size += c.length;
      if (overflowed) {
        if (size > MAX_DRAIN) req.destroy();
        return;
      }
      if (size > MAX_BODY) {
        overflowed = true;
        chunks.length = 0;
        return;
      }
      chunks.push(c);
    });
    req.on("end", () => {
      if (overflowed) reject(new Error("body too large"));
      else resolve(Buffer.concat(chunks).toString("utf8"));
    });
    req.on("error", () => reject(new Error("body read error")));
  });
}

export function makeServer() {
  return http.createServer(async (req, res) => {
    const url = new URL(req.url, "http://relay");
    const now = Date.now();
    try {
      if (req.method === "OPTIONS") {
        res.writeHead(204, CORS);
        res.end();
        return;
      }
      if (url.pathname.startsWith("/api/")) touchPresence(req, now);
      if (req.method === "GET" && url.pathname === "/health") {
        send(res, 200, { ok: true, entries: entries.size });
        return;
      }
      if (req.method === "GET" && url.pathname === "/api/stats") {
        send(res, 200, {
          entries: entries.size,
          reviewed: [...entries.values()].filter((v) => v.rv).length,
          reviewBacklog: reviewPending.size,
          openBounties: bounties.size,
          ipsPresent: presence.size,
          uptimeSec: Math.floor((now - bootAt) / 1000),
          snapshot: {
            enabled: snapshot.enabled,
            loaded: snapshot.loaded,
            pendingFlush: dirty.size + deletedDirty.size,
            lastOkAt: snapshot.lastOkAt,
            lastErr: snapshot.lastErr,
          },
        });
        return;
      }
      if (req.method === "POST" && url.pathname === "/api/lookup") {
        const body = JSON.parse(await readBody(req));
        if (!body || !Array.isArray(body.pairs)) {
          send(res, 400, { error: "expected { pairs: [[first, second], ...] }" });
          return;
        }
        send(res, 200, { ...doLookup(body.pairs), hive: hiveEnvelope(req, now) });
        return;
      }
      if (req.method === "POST" && url.pathname === "/api/contribute") {
        const body = JSON.parse(await readBody(req));
        if (!body || !Array.isArray(body.entries)) {
          send(res, 400, {
            error: "expected { entries: [[first, second, result|null, emoji], ...] }",
          });
          return;
        }
        send(res, 200, { ...doContribute(body.entries), hive: hiveEnvelope(req, now) });
        return;
      }
      if (req.method === "POST" && url.pathname === "/api/bounties") {
        const body = JSON.parse(await readBody(req));
        if (!body || !Array.isArray(body.pairs)) {
          send(res, 400, { error: "expected { pairs: [[first, second], ...] }" });
          return;
        }
        pruneBounties(now);
        send(res, 200, { ...doPostBounties(body.pairs, now), hive: hiveEnvelope(req, now) });
        return;
      }
      if (req.method === "GET" && url.pathname === "/api/bounties") {
        const limit = Number(url.searchParams.get("limit") || "5");
        const session = envStr(req.headers["x-ic-session"] || "");
        const snap = ipSnapshot(req, now);
        send(res, 200, {
          ...doTakeBounties(limit, session, snap, now),
          hive: { peers: snap.spending, cooledUntil: snap.cooledUntil },
        });
        return;
      }
      send(res, 404, { error: "not found" });
    } catch (e) {
      const msg = String(e && e.message ? e.message : e);
      send(res, msg === "body too large" ? 413 : 400, { error: msg });
    }
  });
}

// Test hook: wipe state between cases.
export function _resetForTests() {
  entries.clear();
  dirty.clear();
  deletedDirty.clear();
  presence.clear();
  ipCooldown.clear();
  bounties.clear();
  reviewPending.clear();
}

const isMain = process.argv[1] && import.meta.url.endsWith(process.argv[1].split("/").pop());
if (isMain) {
  await snapshotLoad().catch((e) => {
    snapshot.lastErr = String(e && e.message ? e.message : e);
  });
  startSnapshotLoop();
  makeServer().listen(PORT, () => {
    console.log(
      `relay listening on :${PORT} — ${entries.size} entries` +
        (snapshot.enabled ? ` (${snapshot.loaded} from snapshot)` : " (no snapshot backend)")
    );
  });
}
