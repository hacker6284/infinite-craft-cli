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
//   POST /api/beat        → { nealOk, runId?, cooledUntil? }  (x-ic-session req'd)
//                           → { ok, work }   — THE one client timer (~1s):
//                             liveness in, a work bit out. Sessions, their
//                             runs' bounties, their assignments, and their
//                             place in the household budget split all lapse
//                             beat_lapse_ms after the last beat.
//   GET  /api/work?limit=N → { work: [{kind, first, second}, ...] }
//                             (assigns pair bounties first, review filler
//                              after; assignment sticks while the assignee
//                              beats and reports nealOk)
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
  cooldown_duration_ms as cooldownDurationMs,
  beat_lapse_ms as beatLapseMs,
} from "./_sudo/craft.mjs";

// The longest cooldown the kernel will ever mint (3rd strike = 8h). Used to
// clamp client-supplied cooled-until values so a bad/malicious client can't
// park an IP offline forever.
const MAX_COOLDOWN_MS = Number(cooldownDurationMs(3));

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

// ── beats: the one timer ─────────────────────────────────────────────
// session id → { ip, at, nealOk, runId, workedAt }. Fed exclusively by
// POST /api/beat. Everything liveness-shaped derives from these records
// plus the single kernel threshold beat_lapse_ms — there are no other
// protocol clocks.
const BEAT_LAPSE_MS = Number(beatLapseMs());
const MAX_SESSIONS = 100_000;
const MAX_COOLDOWN_IPS = 50_000;
const sessions = new Map();

function liveSession(id, now) {
  const s = sessions.get(id);
  return s && now - s.at <= BEAT_LAPSE_MS ? s : null;
}

// Lifetime counters for observability (/api/stats, /api/dashboard).
const metrics = {
  lookupPairs: 0, // total pairs asked about
  lookupHits: 0, // of those, served from the hive
  contributed: 0, // new entries accepted
  confirmed: 0, // duplicate-confirmations that advanced review
  healed: 0, // entries dropped by a conflicting claim
  bountiesPosted: 0,
  bountiesTaken: 0,
  reviewsTaken: 0, // peer-review bounties handed to idle clients
};
// ip → epoch-ms until which the IP is cooling down (one session tripped a
// neal 429 — an IP-scoped, hours-long ban). Broadcast to all sessions.
const ipCooldown = new Map();

// ── bounty board ─────────────────────────────────────────────────────
// key → { first, second, ip, session, runId, assignedTo }. A bounty is a
// parallelization offer bound to a live run: it exists exactly as long as
// its poster keeps asserting that runId in beats — no TTLs, no renewal
// bookkeeping, no revoke call. Assignment sticks while the assignee beats
// and reports nealOk; a lapsed or neal-blind assignee forfeits. The poster
// keeps grinding its own list regardless, so reassignment is a speedup
// concern, never a correctness one.
const bounties = new Map();
const MAX_BOUNTIES = 10_000;
const MAX_POST = 500; // bounties per POST (mirrors kernel bounty_sync_plan's slice cap)
const REVIEW_PENDING_MAX = 100_000; // review backlog memory bound
// Unreviewed entries awaiting a peer re-ask of neal (c == 1). Offered to
// idle clients when no pair bounties are open. key → { by } | null — the
// assignment is liveness-scoped, exactly like pair bounties.
const reviewPending = new Map();

function bountyExpired(b, now) {
  const poster = liveSession(b.session, now);
  return !poster || poster.runId !== b.runId;
}

// Claimable by this caller: poster's run still live, not from the caller's
// own IP (household budget is shared), not in the hands of a live,
// neal-capable worker.
function bountyClaimable(b, callerSession, callerIp_, now) {
  if (bountyExpired(b, now)) return false;
  if (b.ip && b.ip === callerIp_) return false;
  if (b.assignedTo && b.assignedTo !== callerSession) {
    const a = liveSession(b.assignedTo, now);
    if (a && a.nealOk) return false;
  }
  return true;
}

function householdRunning(ip, now) {
  for (const s of sessions.values()) {
    if (s.ip === ip && now - s.at <= BEAT_LAPSE_MS && s.runId) return true;
  }
  return false;
}

// Session ids are client-generated; normalize (trim + 64-char cap) ONCE
// here so every map — sessions, bounty.session, assignedTo, review claims —
// stores and looks up the identical key (adversarial finding #1).
function callerSession(req) {
  return envStr(req.headers["x-ic-session"] || "").slice(0, 64);
}

function callerIp(req) {
  // Render terminates TLS and sets X-Forwarded-For with the client IP as the
  // leftmost entry (its documented format), so we read leftmost. That entry
  // is client-spoofable; we do NOT rely on it for anything security-critical
  // — it only groups sessions for the budget split, and the blast radius of a
  // forged IP is bounded by the presence/cooldown caps and the cooled-until
  // clamp below (a spoofer can't grow memory without bound or park an IP
  // offline past MAX_COOLDOWN_MS).
  const fwd = req.headers["x-forwarded-for"];
  if (typeof fwd === "string" && fwd.length) return fwd.split(",")[0].trim();
  return req.socket.remoteAddress || "?";
}

// Evict the oldest key from a Map (insertion-order) to hold a size bound.
function evictOldest(map, cap) {
  while (map.size > cap) {
    const first = map.keys().next().value;
    map.delete(first);
  }
}

export function doBeat(sessionId, ip, body, now) {
  sessionId = String(sessionId).slice(0, 64);
  const prev = sessions.get(sessionId);
  const runId = typeof body.runId === "string" ? body.runId.slice(0, 64) : "";
  // Delete-then-set: a Map.set on an existing key keeps its old insertion
  // position, so without this an actively-beating session could be evicted
  // by the size cap while stale ones linger (adversarial finding #4).
  sessions.delete(sessionId);
  sessions.set(sessionId, {
    ip,
    at: now,
    // Strict boolean: a client with a serialization bug ("false", 0, null)
    // fails CLOSED (treated neal-blind, forfeits assignments) rather than
    // holding work it can't do (adversarial finding #5).
    nealOk: body.nealOk === true,
    runId,
    workedAt: prev ? prev.workedAt : 0,
  });
  evictOldest(sessions, MAX_SESSIONS);
  // The 429 cooldown broadcast rides the beat, clamped to the kernel
  // maximum so a bad value can't park an IP offline indefinitely.
  let until = Number(body.cooledUntil) || 0;
  if (until > now) {
    until = Math.min(until, now + MAX_COOLDOWN_MS);
    if (until > (ipCooldown.get(ip) || 0)) {
      ipCooldown.delete(ip); // keep eviction order = recency (finding #4)
      ipCooldown.set(ip, until);
      evictOldest(ipCooldown, MAX_COOLDOWN_IPS);
    }
  }
  return { ok: true, work: workAvailableFor(sessionId, ip, now) };
}

// The beat's work bit: could this caller usefully pull right now? True for
// live bounties it could claim, or review work it didn't author (bounded
// probe — the pull is the authoritative, exact check).
function workAvailableFor(sessionId, ip, now) {
  if ((ipCooldown.get(ip) || 0) > now) return false;
  if (householdRunning(ip, now)) return false;
  pruneBounties(now);
  for (const b of bounties.values()) {
    if (bountyClaimable(b, sessionId, ip, now)) return true;
  }
  let probed = 0;
  for (const [key, claim] of reviewPending) {
    if (probed++ >= 50) break;
    const entry = entries.get(key);
    if (!entry || entry.rv) {
      reviewPending.delete(key);
      continue;
    }
    if (entry.by === sessionId) continue;
    if (claim && claim.by !== sessionId) {
      const a = liveSession(claim.by, now);
      if (a && a.nealOk) continue;
    }
    return true;
  }
  return false;
}

function ipSnapshot(req, now) {
  const ip = callerIp(req);
  let spending = 0;
  for (const s of sessions.values()) {
    if (s.ip !== ip || now - s.at > BEAT_LAPSE_MS) continue;
    // Spending the household budget: mid-run, or handed hive work within
    // the lapse window (a serving worker's slots are committed).
    if (s.runId || now - s.workedAt <= BEAT_LAPSE_MS) spending++;
  }
  let cooledUntil = ipCooldown.get(ip) || 0;
  if (cooledUntil <= now) {
    ipCooldown.delete(ip);
    cooledUntil = 0;
  }
  return { spending, cooledUntil };
}

/** The hive envelope attached to cache/board responses: what the caller's
    IP looks like right now, so clients can split budget and stand down. */
function hiveEnvelope(req, now) {
  const snap = ipSnapshot(req, now);
  return { peers: snap.spending, cooledUntil: snap.cooledUntil };
}

// A bounty lives exactly as long as its poster keeps asserting its runId.
export function pruneBounties(now) {
  for (const [key, b] of bounties) {
    if (bountyExpired(b, now)) bounties.delete(key);
  }
}

// Housekeeping sweep (server-internal, not a protocol clock): reclaim
// lapsed sessions, spent cooldowns, and orphaned bounties.
function pruneAll(now) {
  for (const [id, s] of sessions) {
    if (now - s.at > BEAT_LAPSE_MS) sessions.delete(id);
  }
  for (const [ip, until] of ipCooldown) {
    if (until <= now) ipCooldown.delete(ip);
  }
  pruneBounties(now);
}

// Live sessions/IPs across the whole relay.
function sessionTotals(now) {
  let live = 0;
  let spending = 0;
  const ips = new Set();
  for (const s of sessions.values()) {
    if (now - s.at > BEAT_LAPSE_MS) continue;
    live++;
    ips.add(s.ip);
    if (s.runId || now - s.workedAt <= BEAT_LAPSE_MS) spending++;
  }
  return { sessions: live, spending, ips: ips.size };
}

function statsPayload(now) {
  let reviewed = 0;
  for (const v of entries.values()) if (v.rv) reviewed++;
  const totals = sessionTotals(now);
  const lookups = metrics.lookupPairs;
  return {
    entries: entries.size,
    reviewed,
    reviewBacklog: reviewPending.size,
    openBounties: bounties.size,
    live: totals, // { sessions, spending, ips }
    metrics: {
      ...metrics,
      hitRate: lookups ? Number((metrics.lookupHits / lookups).toFixed(4)) : 0,
    },
    uptimeSec: Math.floor((now - bootAt) / 1000),
    snapshot: {
      enabled: snapshot.enabled,
      loaded: snapshot.loaded,
      pendingFlush: dirty.size + deletedDirty.size,
      lastOkAt: snapshot.lastOkAt,
      lastErr: snapshot.lastErr,
    },
  };
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
    metrics.lookupPairs++;
    const key = pairKey(a, b);
    const hit = entries.get(key);
    if (hit) {
      results[key] = { r: hit.r, e: hit.e };
      metrics.lookupHits++;
    }
  }
  return { results };
}

// `session` (the contributing client's id, "" for anonymous/direct calls)
// gates peer-review independence: a confirmation only advances review when it
// comes from a session other than the one that last advanced it, so a single
// client can't self-confirm a poisoned result by contributing it twice.
function markDirty(key) {
  if (snapshot.enabled) dirty.add(key);
}
function markDeleted(key) {
  if (snapshot.enabled) deletedDirty.add(key);
}

export function doContribute(list, session = "") {
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
        // A matching claim confirms — but only an INDEPENDENT one (different
        // session than the last confirmer) advances peer review. A repeat
        // from the same session is counted as a dupe and ignored for review,
        // so one client can't lock in its own result.
        // Anonymous callers are NEVER independent — otherwise two bare
        // header-less POSTs self-confirm a fabricated result into a
        // reviewed, heal-immune entry (adversarial finding #2).
        const independent = session !== "" && session !== prev.by;
        if (independent && !prev.rv) {
          prev.c += 1;
          prev.by = session;
          metrics.confirmed++;
          if (prev.c >= 2) {
            prev.rv = true;
            reviewPending.delete(key);
          }
          markDirty(key);
        }
      } else if (!prev.rv) {
        // Conflicting claim against an UNREVIEWED entry: neal is
        // deterministic, so one of the two claims is wrong — drop the
        // entry and re-open the pair as a bounty so the network
        // re-derives it fresh (self-healing beats bookkeeping).
        entries.delete(key);
        dirty.delete(key);
        markDeleted(key);
        reviewPending.delete(key);
        metrics.healed++;
        // v3: no synthetic self-heal bounty — nothing exists on the board
        // without a live poster behind it. The next run that needs this
        // pair will miss, re-derive fresh, and re-post naturally.
      }
      // Conflicts against a reviewed entry are ignored: 2+ independent
      // neal sightings beat a lone dissenter.
      dupes++;
      continue;
    }
    // At capacity: skip the insert but keep scanning — confirmations for
    // already-present keys later in the batch must still land.
    if (entries.size >= MAX_ENTRIES) continue;
    entries.set(key, { r, e, c: 1, rv: false, by: session });
    markDirty(key);
    if (reviewPending.size < REVIEW_PENDING_MAX) reviewPending.set(key, null);
    bounties.delete(key); // a fresh result fulfills any open bounty
    metrics.contributed++;
    added++;
  }
  return { added, dupes, total: entries.size };
}

// ── bounty ops ───────────────────────────────────────────────────────
export function doPostBounties(pairs, now, ip = "", session = "", runId = "") {
  // Pairs already cached come straight back as results (no bounty needed);
  // the rest go on the board bound to the poster's live run.
  const results = {};
  let posted = 0;
  let m = 0;
  pruneBounties(now);
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
    // Already on the board from a live run (prune just cleared the dead
    // ones): first poster wins; a run-change re-post lands as a fresh
    // bounty because the old one expired with its run.
    if (bounties.has(key)) continue;
    if (bounties.size >= MAX_BOUNTIES) continue;
    bounties.set(key, { first: a, second: b, ip, session, runId, assignedTo: "" });
    metrics.bountiesPosted++;
    posted++;
  }
  return { results, posted, open: bounties.size };
}

export function doTakeWork(limit, session, now, ip = "") {
  // Eligibility: not cooling, and nobody in the caller's household mid-run
  // — hive work must never contest a household's own budget.
  if ((ipCooldown.get(ip) || 0) > now) return { work: [], reason: "cooled" };
  if (householdRunning(ip, now)) return { work: [], reason: "ip-active" };
  pruneBounties(now);
  const out = [];
  // limit=0 is an explicit "nothing" (0 is falsy — `limit || 5` silently
  // upgraded it to the default and assigned unwanted work; finding #3).
  const cap = Math.max(0, Math.min(Number.isFinite(limit) ? limit : 5, 20));
  if (cap === 0) return { work: out };
  for (const b of bounties.values()) {
    if (out.length >= cap) break;
    if (!bountyClaimable(b, session, ip, now)) continue;
    if (b.assignedTo !== session) metrics.bountiesTaken++;
    b.assignedTo = session || "?";
    out.push({ kind: "pair", first: b.first, second: b.second });
  }
  // Idle capacity beyond open bounties goes to peer review: re-ask neal
  // about unreviewed entries. The expected answer is never shared — the
  // reviewer's independent contribute confirms or heals the entry. Never
  // handed to the session that authored the sighting.
  if (out.length < cap) {
    for (const [key, claim] of reviewPending) {
      if (out.length >= cap) break;
      const entry = entries.get(key);
      if (!entry || entry.rv) {
        reviewPending.delete(key);
        continue;
      }
      if (entry.by === session) continue;
      if (claim && claim.by !== session) {
        const a = liveSession(claim.by, now);
        if (a && a.nealOk) continue;
      }
      const i2 = key.indexOf("\0");
      if (i2 < 0) continue;
      if (!claim || claim.by !== session) metrics.reviewsTaken++;
      reviewPending.set(key, { by: session || "?" });
      out.push({ kind: "review", first: key.slice(0, i2), second: key.slice(i2 + 1) });
    }
  }
  const s = sessions.get(session);
  if (s && out.length) s.workedAt = now;
  return { work: out };
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
          entries.set(flat[i], { r: v.r ?? null, e: v.e || "", c, rv, by: v.by || "" });
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

// ── dashboard: a tiny honey-themed status page (self-refresh, no deps) ─
function dashboardHtml(s) {
  const m = s.metrics;
  const pct = (m.hitRate * 100).toFixed(1);
  const rows = [
    ["Cache entries", s.entries.toLocaleString()],
    ["Peer-reviewed", `${s.reviewed.toLocaleString()} (${s.entries ? ((s.reviewed / s.entries) * 100).toFixed(0) : 0}%)`],
    ["Review backlog", s.reviewBacklog.toLocaleString()],
    ["Open bounties", s.openBounties.toLocaleString()],
    ["Live sessions", `${s.live.sessions} (${s.live.spending} spending) · ${s.live.ips} IPs`],
    ["Lookup hit rate", `${pct}% of ${m.lookupPairs.toLocaleString()}`],
    ["Contributed", m.contributed.toLocaleString()],
    ["Confirmations", m.confirmed.toLocaleString()],
    ["Self-healed", m.healed.toLocaleString()],
    ["Bounties posted / taken", `${m.bountiesPosted.toLocaleString()} / ${m.bountiesTaken.toLocaleString()}`],
    ["Reviews served", m.reviewsTaken.toLocaleString()],
    ["Snapshot", s.snapshot.enabled ? (s.snapshot.lastErr ? "error: " + s.snapshot.lastErr : "ok") : "disabled"],
    ["Uptime", `${Math.floor(s.uptimeSec / 3600)}h ${Math.floor((s.uptimeSec % 3600) / 60)}m`],
  ];
  const cells = rows
    .map(
      ([k, v]) =>
        `<div class="cell"><div class="k">${k}</div><div class="v">${String(v)}</div></div>`
    )
    .join("");
  return `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="10">
<title>🐝 Infinite Craft hive</title>
<style>
  :root{--bg:#12100a;--panel:#1c1810;--line:#2e2716;--text:#f0e6d2;--muted:#a99a76;--honey:#ffb300;--honey2:#ffcf4d}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;padding:32px 20px}
  main{max-width:640px;margin:0 auto}
  h1{font-size:20px;margin:0 0 2px;letter-spacing:.02em}
  .sub{color:var(--muted);font-size:13px;margin:0 0 24px}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}
  .cell{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px}
  .k{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.09em;margin-bottom:4px}
  .v{color:var(--honey2);font-size:18px;font-variant-numeric:tabular-nums}
  footer{color:var(--muted);font-size:12px;margin-top:22px}
</style></head><body><main>
  <h1>🐝 Infinite Craft hive</h1>
  <p class="sub">Shared pair-result cache · auto-refreshes every 10s</p>
  <div class="grid">${cells}</div>
  <footer>Raw JSON at <code>/api/stats</code></footer>
</main></body></html>`;
}

// ── http plumbing ────────────────────────────────────────────────────
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  // The clients attach the session header on every request; without it here
  // the browser trainer's cross-origin calls fail CORS preflight and the
  // hive tier is silently disabled in the browser host.
  "Access-Control-Allow-Headers": "Content-Type, x-ic-session",
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
      if (req.method === "GET" && url.pathname === "/health") {
        send(res, 200, { ok: true, entries: entries.size });
        return;
      }
      if (req.method === "GET" && url.pathname === "/api/stats") {
        send(res, 200, statsPayload(now));
        return;
      }
      if (req.method === "GET" && url.pathname === "/api/dashboard") {
        res.writeHead(200, { "Content-Type": "text/html; charset=utf-8", ...CORS });
        res.end(dashboardHtml(statsPayload(now)));
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
        send(res, 200, {
          ...doContribute(body.entries, callerSession(req)),
          hive: hiveEnvelope(req, now),
        });
        return;
      }
      if (req.method === "POST" && url.pathname === "/api/beat") {
        const body = JSON.parse(await readBody(req));
        const session = callerSession(req);
        if (!session) {
          send(res, 400, { error: "x-ic-session required" });
          return;
        }
        send(res, 200, doBeat(session, callerIp(req), body || {}, now));
        return;
      }
      if (req.method === "GET" && url.pathname === "/api/work") {
        const limit = Number(url.searchParams.get("limit") || "5");
        const session = callerSession(req);
        send(res, 200, {
          ...doTakeWork(limit, session, now, callerIp(req)),
          hive: hiveEnvelope(req, now),
        });
        return;
      }
      if (req.method === "POST" && url.pathname === "/api/bounties") {
        const body = JSON.parse(await readBody(req));
        if (!body || !Array.isArray(body.pairs)) {
          send(res, 400, { error: "expected { pairs: [[first, second], ...] }" });
          return;
        }
        send(res, 200, {
          ...doPostBounties(
            body.pairs,
            now,
            callerIp(req),
            callerSession(req),
            typeof body.runId === "string" ? body.runId.slice(0, 64) : ""
          ),
          hive: hiveEnvelope(req, now),
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
  sessions.clear();
  ipCooldown.clear();
  bounties.clear();
  reviewPending.clear();
  for (const k of Object.keys(metrics)) metrics[k] = 0;
}

// Test hook: inspect a bounty record (lease/TTL/renewal assertions).
export function _bountyForTests(first, second) {
  return bounties.get(pairKey(sanitizeName(first), sanitizeName(second)));
}

const isMain = process.argv[1] && import.meta.url.endsWith(process.argv[1].split("/").pop());
if (isMain) {
  await snapshotLoad().catch((e) => {
    snapshot.lastErr = String(e && e.message ? e.message : e);
  });
  startSnapshotLoop();
  setInterval(() => pruneAll(Date.now()), 60_000).unref();
  makeServer().listen(PORT, () => {
    console.log(
      `relay listening on :${PORT} — ${entries.size} entries` +
        (snapshot.enabled ? ` (${snapshot.loaded} from snapshot)` : " (no snapshot backend)")
    );
  });
}
