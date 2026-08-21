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

/** key "<a>\0<b>" → { r: string|null, e: string, c: number } */
const entries = new Map();
/** keys written since the last successful snapshot flush */
const dirty = new Set();
const bootAt = Date.now();
const snapshot = {
  enabled: Boolean(UPSTASH_URL && UPSTASH_TOKEN),
  loaded: 0,
  lastOkAt: null,
  lastErr: null,
};

// ── canonicalization ─────────────────────────────────────────────────
// Must agree with the kernel's pair_key: code-POINT lexicographic order
// (Python str compare), not JS's default UTF-16 code-unit order — they
// differ once astral characters appear in names.
export function lexCompareCodepoints(a, b) {
  const ia = a[Symbol.iterator]();
  const ib = b[Symbol.iterator]();
  for (;;) {
    const na = ia.next();
    const nb = ib.next();
    if (na.done && nb.done) return 0;
    if (na.done) return -1;
    if (nb.done) return 1;
    const ca = na.value.codePointAt(0);
    const cb = nb.value.codePointAt(0);
    if (ca !== cb) return ca < cb ? -1 : 1;
  }
}

export function pairKey(a, b) {
  return lexCompareCodepoints(a, b) <= 0 ? a + "\0" + b : b + "\0" + a;
}

// Storage normalization, mirroring the kernel's sanitize_element_name:
// strip, drop C0 controls / DEL / C1 controls / U+2028 / U+2029.
export function sanitizeName(raw) {
  if (typeof raw !== "string") return "";
  let out = "";
  for (const ch of raw.trim()) {
    const cp = ch.codePointAt(0);
    if (cp < 0x20 || (cp >= 0x7f && cp <= 0x9f) || cp === 0x2028 || cp === 0x2029) continue;
    out += ch;
  }
  return out.length > MAX_NAME ? "" : out;
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
      // First write wins; an independent identical claim is a confirmation
      // (the raw material for phase-3 peer review), a conflicting one is
      // recorded nowhere — peer review, not last-write, resolves disputes.
      if (prev.r === r) {
        prev.c += 1;
        dirty.add(key);
      }
      dupes++;
      continue;
    }
    if (entries.size >= MAX_ENTRIES) break;
    entries.set(key, { r, e, c: 1 });
    dirty.add(key);
    added++;
  }
  return { added, dupes, total: entries.size };
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
          entries.set(flat[i], { r: v.r ?? null, e: v.e || "", c: v.c || 1 });
          snapshot.loaded++;
        }
      } catch {
        /* skip corrupt field */
      }
    }
  } while (cursor !== "0");
}

async function snapshotFlush() {
  if (!snapshot.enabled || dirty.size === 0) return;
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

function readBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on("data", (c) => {
      size += c.length;
      if (size > MAX_BODY) {
        reject(new Error("body too large"));
        req.destroy();
        return;
      }
      chunks.push(c);
    });
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

export function makeServer() {
  return http.createServer(async (req, res) => {
    const url = new URL(req.url, "http://relay");
    try {
      if (req.method === "OPTIONS") {
        res.writeHead(204, CORS);
        res.end();
        return;
      }
      if (req.method === "GET" && url.pathname === "/health") {
        send(res, 200, { ok: true, entries: entries.size });
        return;
      }
      if (req.method === "GET" && url.pathname === "/api/stats") {
        send(res, 200, {
          entries: entries.size,
          uptimeSec: Math.floor((Date.now() - bootAt) / 1000),
          snapshot: {
            enabled: snapshot.enabled,
            loaded: snapshot.loaded,
            pendingFlush: dirty.size,
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
        send(res, 200, doLookup(body.pairs));
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
        send(res, 200, doContribute(body.entries));
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
