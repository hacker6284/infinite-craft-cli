// Unit + HTTP tests for the hive-mind relay. Runs with `node --test`;
// no Upstash env vars set, so the snapshot backend stays disabled.
import test from "node:test";
import assert from "node:assert/strict";
import {
  pairKey,
  sanitizeName,
  doLookup,
  doContribute,
  makeServer,
  envStr,
  doBeat,
  doTakeWork,
  doPostBounties,
  pruneBounties,
  _resetForTests,
  _bountyForTests,
} from "./server.mjs";
import { beat_lapse_ms as beatLapseMs } from "./_sudo/craft.mjs";

const NUL = String.fromCharCode(0);

test("pairKey canonicalizes by code point", () => {
  assert.equal(pairKey("Water", "Fire"), "Fire" + NUL + "Water");
  assert.equal(pairKey("Fire", "Water"), "Fire" + NUL + "Water");
  assert.equal(pairKey("A", "A"), "A" + NUL + "A");
  // Astral vs BMP: code-point order (Python semantics), not UTF-16 units —
  // U+FFFD sorts BELOW U+1F600 by code point (UTF-16 units say otherwise).
  assert.equal(pairKey("\u{1F600}", "\uFFFD"), "\uFFFD" + NUL + "\u{1F600}");
});

test("envStr strips wrapping quotes and whitespace", () => {
  assert.equal(envStr('"https://x.upstash.io"'), "https://x.upstash.io");
  assert.equal(envStr("'tok'"), "tok");
  assert.equal(envStr("  plain  "), "plain");
  assert.equal(envStr(undefined), "");
  assert.equal(envStr('"'), '"');
});

test("sanitizeName strips controls and trims", () => {
  assert.equal(sanitizeName("  Steam \u0007 Engine\u2028  "), "Steam  Engine");
  assert.equal(sanitizeName("\u0000\u0001"), "");
  assert.equal(sanitizeName(42), "");
  assert.equal(sanitizeName("x".repeat(300)), "");
  // Length bound is measured in code points, not UTF-16 units: 150 astral
  // chars (300 UTF-16 units) must survive; 250 code points must not.
  const bees = "\u{1F41D}".repeat(150);
  assert.equal(sanitizeName(bees), bees);
  assert.equal(sanitizeName("\u{1F41D}".repeat(250)), "");
});

test("oversized body gets a real 413, not a connection reset", async () => {
  _resetForTests();
  const server = makeServer();
  await new Promise((resolve) => server.listen(0, resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    const big = JSON.stringify({ pairs: [["A", "B".repeat(9 * 1024 * 1024)]] });
    const resp = await fetch(`${base}/api/lookup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: big,
    });
    assert.equal(resp.status, 413);
    assert.equal((await resp.json()).error, "body too large");
    // Server must still be healthy afterward.
    const health = await (await fetch(`${base}/health`)).json();
    assert.equal(health.ok, true);
  } finally {
    server.close();
  }
});

test("contribute then lookup round-trip; first write wins", () => {
  _resetForTests();
  const r1 = doContribute([
    ["Fire", "Water", "Steam", "\u{1F4A8}"],
    ["Water", "Fire", "Steam", ""], // same pair reversed → same-session dupe
    ["Earth", "Earth", null, ""], // Nothing result
    ["Bad", "", "X", ""], // empty name → dropped
  ], "sA");
  assert.equal(r1.added, 2);
  assert.equal(r1.dupes, 1);
  // An independent session's matching claim reviews the entry...
  doContribute([["Water", "Fire", "Steam", ""]], "sB");

  // ...so a conflicting claim neither overwrites nor heals it.
  const r2 = doContribute([["Fire", "Water", "Poison", ""]], "sC");
  assert.equal(r2.added, 0);
  assert.equal(r2.dupes, 1);

  const found = doLookup([
    ["Water", "Fire"],
    ["Earth", "Earth"],
    ["No", "Hit"],
  ]);
  const steam = found.results["Fire" + NUL + "Water"];
  assert.deepEqual(steam, { r: "Steam", e: "\u{1F4A8}" });
  assert.deepEqual(found.results["Earth" + NUL + "Earth"], { r: null, e: "" });
  assert.equal(Object.keys(found.results).length, 2);
});

test("http endpoints: health, lookup, contribute, stats, cors", async () => {
  _resetForTests();
  const server = makeServer();
  await new Promise((resolve) => server.listen(0, resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    const health = await (await fetch(`${base}/health`)).json();
    assert.equal(health.ok, true);

    const pre = await fetch(`${base}/api/lookup`, { method: "OPTIONS" });
    assert.equal(pre.status, 204);
    assert.equal(pre.headers.get("access-control-allow-origin"), "*");

    const contrib = await fetch(`${base}/api/contribute`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entries: [["Fire", "Water", "Steam", ""]] }),
    });
    assert.equal((await contrib.json()).added, 1);

    const look = await fetch(`${base}/api/lookup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pairs: [["Water", "Fire"]] }),
    });
    const body = await look.json();
    assert.equal(body.results["Fire" + NUL + "Water"].r, "Steam");

    const stats = await (await fetch(`${base}/api/stats`)).json();
    assert.equal(stats.entries, 1);
    assert.equal(stats.snapshot.enabled, false);

    const bad = await fetch(`${base}/api/lookup`, {
      method: "POST",
      body: JSON.stringify({ nope: true }),
    });
    assert.equal(bad.status, 400);

    const missing = await fetch(`${base}/nope`);
    assert.equal(missing.status, 404);
  } finally {
    server.close();
  }
});


// ── the one-timer board ──────────────────────────────────────────────
// One clock (the ~1s client beat), one threshold (beat_lapse_ms). Tests
// drive time explicitly through the `now` params.

const T0 = 1_000_000;
const LAPSE = Number(beatLapseMs());

function beat(session, ip, body = {}, now = T0) {
  // Real clients always send the explicit boolean; the relay is strict
  // about it (a garbage value fails closed to neal-blind).
  return doBeat(session, ip, { nealOk: true, ...body }, now);
}

test("beat registers a session; work bit false on an empty board", () => {
  _resetForTests();
  const r = beat("s1", "1.1.1.1");
  assert.equal(r.ok, true);
  assert.equal(r.work, false);
});

test("bounty lifecycle: run posts, foreign worker assigned, contribute fulfills", () => {
  _resetForTests();
  beat("poster", "1.1.1.1", { runId: "r1" });
  const post = doPostBounties([["Fire", "Water"]], T0, "1.1.1.1", "poster", "r1");
  assert.equal(post.posted, 1);
  assert.equal(beat("worker", "2.2.2.2").work, true);
  const take = doTakeWork(5, "worker", T0, "2.2.2.2");
  assert.equal(take.work.length, 1);
  assert.equal(take.work[0].kind, "pair");
  assert.equal(take.work[0].first, "Fire");
  // Assigned: a second worker is handed nothing while the assignee is
  // live and neal-capable.
  beat("worker2", "3.3.3.3");
  assert.equal(doTakeWork(5, "worker2", T0, "3.3.3.3").work.length, 0);
  // The result fulfills the bounty.
  doContribute([["Fire", "Water", "Steam", ""]], "worker");
  assert.equal(_bountyForTests("Fire", "Water"), undefined);
});

test("assignment forfeits on neal-blind report and on assignee lapse", () => {
  _resetForTests();
  beat("poster", "1.1.1.1", { runId: "r1" });
  doPostBounties([["A", "B"]], T0, "1.1.1.1", "poster", "r1");
  beat("w1", "2.2.2.2");
  assert.equal(doTakeWork(5, "w1", T0, "2.2.2.2").work.length, 1);
  beat("w2", "3.3.3.3");
  assert.equal(doTakeWork(5, "w2", T0 + 1000, "3.3.3.3").work.length, 0);
  // w1 reports it cannot reach neal → its assignment is forfeit.
  beat("poster", "1.1.1.1", { runId: "r1" }, T0 + 2000);
  beat("w1", "2.2.2.2", { nealOk: false }, T0 + 2000);
  beat("w2", "3.3.3.3", {}, T0 + 2000);
  assert.equal(doTakeWork(5, "w2", T0 + 2000, "3.3.3.3").work.length, 1);
  // w2 lapses silently → forfeits the same way.
  const later = T0 + 2000 + LAPSE + 1000;
  beat("poster", "1.1.1.1", { runId: "r1" }, later);
  beat("w3", "4.4.4.4", {}, later);
  assert.equal(doTakeWork(5, "w3", later, "4.4.4.4").work.length, 1);
});

test("bounties lapse with the poster's run: runId gone, runId changed, beats gone", () => {
  _resetForTests();
  beat("poster", "1.1.1.1", { runId: "r1" });
  doPostBounties([["A", "B"], ["C", "D"]], T0, "1.1.1.1", "poster", "r1");
  // Run over (cancelled or done): beats continue without the runId.
  beat("poster", "1.1.1.1", {}, T0 + 1000);
  pruneBounties(T0 + 1000);
  assert.equal(_bountyForTests("A", "B"), undefined);
  assert.equal(_bountyForTests("C", "D"), undefined);
  // A new run re-posts cleanly.
  beat("poster", "1.1.1.1", { runId: "r2" }, T0 + 2000);
  assert.equal(doPostBounties([["A", "B"]], T0 + 2000, "1.1.1.1", "poster", "r2").posted, 1);
  // Poster silence past the lapse threshold clears the board too.
  pruneBounties(T0 + 2000 + LAPSE + 1);
  assert.equal(_bountyForTests("A", "B"), undefined);
});

test("re-post across runs is the rebind: fresh bounty, never a duplicate", () => {
  _resetForTests();
  beat("poster", "1.1.1.1", { runId: "r1" });
  doPostBounties([["A", "B"]], T0, "1.1.1.1", "poster", "r1");
  // The old run's bounty died with its runId; the new run's sync re-posts.
  beat("poster", "1.1.1.1", { runId: "r2" }, T0 + 1000);
  const again = doPostBounties([["A", "B"]], T0 + 1000, "1.1.1.1", "poster", "r2");
  assert.equal(again.posted, 1);
  assert.equal(again.open, 1); // exactly one board entry, bound to r2
  pruneBounties(T0 + 1000);
  assert.notEqual(_bountyForTests("A", "B"), undefined);
  // Re-post within the SAME run is a no-op.
  assert.equal(doPostBounties([["A", "B"]], T0 + 2000, "1.1.1.1", "poster", "r2").posted, 0);
});

test("work bit: household run false, own review false, foreign true, cooling false", () => {
  _resetForTests();
  beat("poster", "1.1.1.1", { runId: "r1" });
  doPostBounties([["A", "B"]], T0, "1.1.1.1", "poster", "r1");
  assert.equal(beat("sib", "1.1.1.1").work, false); // household mid-run
  assert.equal(beat("w1", "2.2.2.2").work, true); // foreign idle
  beat("cold", "5.5.5.5", { cooledUntil: T0 + 3_600_000 });
  assert.equal(beat("cold", "5.5.5.5").work, false); // cooling IP
  _resetForTests();
  doContribute([["X", "Y", "Z", ""]], "author");
  beat("author", "6.6.6.6");
  assert.equal(beat("author", "6.6.6.6").work, false); // own sighting
  assert.equal(beat("other", "7.7.7.7").work, true); // reviewable by others
});

test("no work handed while the caller's household is mid-run; cooled refused", () => {
  _resetForTests();
  beat("posterA", "1.1.1.1", { runId: "rA" });
  doPostBounties([["A", "B"]], T0, "1.1.1.1", "posterA", "rA");
  beat("posterB", "2.2.2.2", { runId: "rB" });
  const busy = doTakeWork(5, "sibB", T0, "2.2.2.2");
  assert.equal(busy.work.length, 0);
  assert.equal(busy.reason, "ip-active");
  beat("cool", "9.9.9.9", { cooledUntil: T0 + 3_600_000 });
  const cooled = doTakeWork(5, "cool2", T0, "9.9.9.9");
  assert.equal(cooled.reason, "cooled");
});

test("mismatch against unreviewed entry heals: delete, no synthetic bounty", () => {
  _resetForTests();
  doContribute([["P", "Q", "R1", ""]], "sA");
  doContribute([["P", "Q", "R2", ""]], "sB");
  assert.equal(Object.keys(doLookup([["P", "Q"]]).results).length, 0);
  assert.equal(_bountyForTests("P", "Q"), undefined);
});

test("review work: handed to non-authors only, assignment liveness-scoped", () => {
  _resetForTests();
  doContribute([["X", "Y", "Z", ""]], "author");
  beat("author", "1.1.1.1");
  assert.equal(doTakeWork(5, "author", T0, "1.1.1.1").work.length, 0);
  beat("w1", "2.2.2.2");
  const take = doTakeWork(5, "w1", T0, "2.2.2.2");
  assert.equal(take.work.length, 1);
  assert.equal(take.work[0].kind, "review");
  // Assigned to a live w1 → withheld from w2; w1 lapses → reassigned.
  beat("w2", "3.3.3.3");
  assert.equal(doTakeWork(5, "w2", T0 + 1000, "3.3.3.3").work.length, 0);
  const later = T0 + LAPSE + 2000;
  beat("w2", "3.3.3.3", {}, later);
  assert.equal(doTakeWork(5, "w2", later, "3.3.3.3").work.length, 1);
});

test("peers envelope counts spending sessions from beats", async () => {
  _resetForTests();
  const now = Date.now();
  doBeat("runner", "8.8.8.8", { runId: "run" }, now);
  doBeat("idler", "8.8.8.8", {}, now);
  const server = makeServer();
  await new Promise((r) => server.listen(0, r));
  const port = server.address().port;
  try {
    const resp = await fetch(`http://127.0.0.1:${port}/api/lookup`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-forwarded-for": "8.8.8.8" },
      body: JSON.stringify({ pairs: [["A", "B"]] }),
    });
    const body = await resp.json();
    assert.equal(body.hive.peers, 1); // the runner spends; the idler doesn't
  } finally {
    server.close();
  }
});

test("HTTP: beat requires a session; beat + post + work round trip", async () => {
  _resetForTests();
  const server = makeServer();
  await new Promise((r) => server.listen(0, r));
  const port = server.address().port;
  const base = `http://127.0.0.1:${port}`;
  const post = (path, body, headers = {}) =>
    fetch(base + path, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers },
      body: JSON.stringify(body),
    });
  try {
    const noSession = await post("/api/beat", { nealOk: true });
    assert.equal(noSession.status, 400);
    const pb = await post("/api/beat", { nealOk: true, runId: "r1" },
      { "x-ic-session": "poster", "x-forwarded-for": "1.1.1.1" });
    assert.equal((await pb.json()).ok, true);
    await post("/api/bounties", { pairs: [["Fire", "Water"]], runId: "r1" },
      { "x-ic-session": "poster", "x-forwarded-for": "1.1.1.1" });
    const wb = await post("/api/beat", { nealOk: true },
      { "x-ic-session": "worker", "x-forwarded-for": "2.2.2.2" });
    assert.equal((await wb.json()).work, true);
    const take = await fetch(base + "/api/work?limit=5",
      { headers: { "x-ic-session": "worker", "x-forwarded-for": "2.2.2.2" } });
    const got = await take.json();
    assert.equal(got.work.length, 1);
    assert.equal(got.work[0].kind, "pair");
  } finally {
    server.close();
  }
});

test("stats + dashboard render on the beat model", async () => {
  _resetForTests();
  const now = Date.now();
  doBeat("poster", "1.1.1.1", { runId: "r1" }, now);
  doPostBounties([["A", "B"]], now, "1.1.1.1", "poster", "r1");
  const server = makeServer();
  await new Promise((r) => server.listen(0, r));
  const port = server.address().port;
  try {
    const stats = await (await fetch(`http://127.0.0.1:${port}/api/stats`)).json();
    assert.equal(stats.openBounties, 1);
    assert.equal(stats.live.sessions, 1);
    assert.equal(stats.live.spending, 1);
    const dash = await fetch(`http://127.0.0.1:${port}/api/dashboard`);
    assert.equal(dash.status, 200);
    const html = await dash.text();
    assert.ok(html.includes("Bounties posted / taken"));
  } finally {
    server.close();
  }
});

test("anonymous contributions never self-confirm (poisoning regression)", () => {
  _resetForTests();
  doContribute([["Poison", "Ivy", "FAKE", "☠️"]], "");
  doContribute([["Poison", "Ivy", "FAKE", "☠️"]], ""); // same anon caller repeats
  // Still unreviewed → a sessioned conflicting claim heals it away.
  doContribute([["Poison", "Ivy", "TRUE", ""]], "legit");
  assert.equal(Object.keys(doLookup([["Poison", "Ivy"]]).results).length, 0);
});

test("long session ids are normalized consistently end to end", async () => {
  _resetForTests();
  const long = "L".repeat(80);
  const server = makeServer();
  await new Promise((r) => server.listen(0, r));
  const port = server.address().port;
  const base = `http://127.0.0.1:${port}`;
  try {
    await fetch(base + "/api/beat", {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-ic-session": long,
                 "x-forwarded-for": "1.1.1.1" },
      body: JSON.stringify({ nealOk: true, runId: "r1" }),
    });
    await fetch(base + "/api/bounties", {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-ic-session": long,
                 "x-forwarded-for": "1.1.1.1" },
      body: JSON.stringify({ pairs: [["Fire", "Water"]], runId: "r1" }),
    });
    // The long-id poster is LIVE — its bounty must be claimable elsewhere.
    const take = await fetch(base + "/api/work?limit=5",
      { headers: { "x-ic-session": "worker", "x-forwarded-for": "2.2.2.2" } });
    assert.equal((await take.json()).work.length, 1);
  } finally {
    server.close();
  }
});

test("limit=0 assigns nothing; garbage nealOk fails closed", () => {
  _resetForTests();
  beat("poster", "1.1.1.1", { runId: "r1" });
  doPostBounties([["A", "B"]], T0, "1.1.1.1", "poster", "r1");
  beat("w0", "2.2.2.2");
  assert.equal(doTakeWork(0, "w0", T0, "2.2.2.2").work.length, 0);
  // w1 claims, then beats with a STRING "false" — strict typing treats it
  // as neal-blind, so the assignment forfeits rather than stranding.
  beat("w1", "3.3.3.3");
  assert.equal(doTakeWork(5, "w1", T0, "3.3.3.3").work.length, 1);
  doBeat("w1", "3.3.3.3", { nealOk: "false" }, T0 + 1000);
  beat("poster", "1.1.1.1", { runId: "r1" }, T0 + 1000);
  beat("w2", "4.4.4.4", {}, T0 + 1000);
  assert.equal(doTakeWork(5, "w2", T0 + 1000, "4.4.4.4").work.length, 1);
});

test("re-beating refreshes eviction order; self-re-pull doesn't inflate metrics", async () => {
  _resetForTests();
  // Eviction-order refresh: set-on-existing-key must move to the end.
  beat("active", "1.1.1.1", { runId: "r1" });
  beat("stale", "2.2.2.2");
  beat("active", "1.1.1.1", { runId: "r1" }, T0 + 1000); // re-beat
  // Metrics: pulling the same still-open item twice counts once.
  doPostBounties([["A", "B"]], T0 + 1000, "1.1.1.1", "active", "r1");
  beat("w1", "3.3.3.3", {}, T0 + 1000);
  doTakeWork(5, "w1", T0 + 1000, "3.3.3.3");
  doTakeWork(5, "w1", T0 + 1000, "3.3.3.3"); // idempotent self-re-pull
  const server = makeServer();
  await new Promise((r) => server.listen(0, r));
  const port = server.address().port;
  try {
    const stats = await (await fetch(`http://127.0.0.1:${port}/api/stats`)).json();
    assert.equal(stats.metrics.bountiesTaken, 1);
  } finally {
    server.close();
  }
});
