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
  doTakeBounties,
  doPostBounties,
  pruneBounties,
  _resetForTests,
  _bountyForTests,
} from "./server.mjs";
import {
  bounty_lease_ttl_ms as bountyLeaseTtlMs,
  bounty_legacy_ttl_ms as bountyLegacyTtlMs,
  bounty_session_quota as bountySessionQuota,
  bounty_poll_hint_ms as bountyPollHintMs,
} from "./_sudo/craft.mjs";

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
    ["Water", "Fire", "Steam", ""], // same pair reversed → confirmation
    ["Earth", "Earth", null, ""], // Nothing result
    ["Bad", "", "X", ""], // empty name → dropped
  ]);
  assert.equal(r1.added, 2);
  assert.equal(r1.dupes, 1);

  // Conflicting claim neither overwrites nor confirms.
  const r2 = doContribute([["Fire", "Water", "Poison", ""]]);
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


test("bounty lifecycle: post, take, fulfill via contribute", async () => {
  _resetForTests();
  const server = makeServer();
  await new Promise((resolve) => server.listen(0, resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const H = (session, state, ip) => ({
    "Content-Type": "application/json",
    "x-ic-session": session,
    "x-ic-state": state,
    "x-forwarded-for": ip,
  });
  try {
    // Seed one cached pair so posting returns it as a result, not a bounty.
    doContribute([["Fire", "Water", "Steam", ""]]);
    // Poster on IP A (running) posts three pairs; one is already cached.
    const post = await (
      await fetch(`${base}/api/bounties`, {
        method: "POST",
        headers: H("poster", "running", "1.1.1.1"),
        body: JSON.stringify({ pairs: [["Water", "Fire"], ["Earth", "Wind"], ["Lava", "Sea"]] }),
      })
    ).json();
    assert.equal(post.posted, 2);
    assert.equal(Object.keys(post.results).length, 1);

    // A sibling on the SAME IP is refused while the poster is running.
    const refused = await (
      await fetch(`${base}/api/bounties?limit=5`, { headers: H("sibling", "idle", "1.1.1.1") })
    ).json();
    assert.equal(refused.bounties.length, 0);
    assert.equal(refused.reason, "ip-active");
    assert.equal(refused.hive.peers, 1);

    // Poster goes idle. A helper on a DIFFERENT IP claims both pair bounties
    // (plus the review bounty for the seeded unreviewed entry). Same-IP would
    // be refused — a poster is never handed its own bounties back.
    await fetch(`${base}/api/lookup`, {
      method: "POST",
      headers: H("poster", "idle", "1.1.1.1"),
      body: JSON.stringify({ pairs: [] }),
    });
    const take = await (
      await fetch(`${base}/api/bounties?limit=5`, { headers: H("helper", "serving", "2.2.2.2") })
    ).json();
    const kinds = take.bounties.map((b) => b.kind).sort();
    assert.deepEqual(kinds, ["pair", "pair", "review"]);
    const review = take.bounties.find((b) => b.kind === "review");
    assert.equal(review.first, "Fire");
    assert.equal(review.second, "Water");

    // Claimed bounties are not re-offered inside the claim TTL.
    const again = await (
      await fetch(`${base}/api/bounties?limit=5`, { headers: H("helper2", "idle", "2.2.2.2") })
    ).json();
    assert.equal(again.bounties.length, 0);

    // Helper fulfills one pair bounty + confirms the review → entry reviewed.
    const fulfil = await (
      await fetch(`${base}/api/contribute`, {
        method: "POST",
        headers: H("helper", "serving", "2.2.2.2"),
        body: JSON.stringify({
          entries: [
            ["Earth", "Wind", "Dust", ""],
            ["Fire", "Water", "Steam", ""],
          ],
        }),
      })
    ).json();
    assert.equal(fulfil.added, 1);
    assert.equal(fulfil.dupes, 1);
    const stats = await (await fetch(`${base}/api/stats`)).json();
    assert.equal(stats.reviewed, 1);
    assert.equal(stats.openBounties, 1); // Lava+Sea still open (claimed)
  } finally {
    server.close();
  }
});

test("a bounty is never handed back to the IP that posted it", () => {
  _resetForTests();
  const now = Date.now();
  // Poster on IP A posts one bounty.
  const posted = doPostBounties([["Moon", "Star"]], now, "1.1.1.1");
  assert.equal(posted.posted, 1);
  // Same IP asking for work gets nothing (would be self-serving its own
  // cancelled/leftover bounty on the shared per-IP budget).
  const self = doTakeBounties(5, "sA", { cooledUntil: 0, running: 0 }, now, "1.1.1.1");
  assert.equal(self.bounties.length, 0);
  // A different IP is offered it.
  const other = doTakeBounties(5, "sB", { cooledUntil: 0, running: 0 }, now, "2.2.2.2");
  assert.equal(other.bounties.length, 1);
  assert.equal(other.bounties[0].first, "Moon");
});

test("mismatch against unreviewed entry heals: delete + reopen bounty", () => {
  _resetForTests();
  doContribute([["Fire", "Water", "Steam", ""]]);
  // Conflicting claim → entry dropped, pair back on the board.
  const r = doContribute([["Fire", "Water", "Poison", ""]]);
  assert.equal(r.dupes, 1);
  const gone = doLookup([["Fire", "Water"]]);
  assert.equal(Object.keys(gone.results).length, 0);
  const board = doTakeBounties(5, "s", { cooledUntil: 0, running: 0 }, Date.now());
  assert.equal(board.bounties.length, 1);
  assert.equal(board.bounties[0].kind, "pair");
  // Reviewed entries shrug off dissenters.
  doContribute([["A", "B", "X", ""]]);
  doContribute([["A", "B", "X", ""]]); // review passes → rv
  doContribute([["A", "B", "Y", ""]]); // dissent ignored
  const still = doLookup([["A", "B"]]);
  assert.equal(Object.values(still.results)[0].r, "X");
});

test("cooldown broadcast: one cooled session stands the IP down", async () => {
  _resetForTests();
  const server = makeServer();
  await new Promise((resolve) => server.listen(0, resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    const until = Date.now() + 3600_000;
    await fetch(`${base}/api/lookup`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-ic-session": "laptop1",
        "x-ic-state": "cooled",
        "x-ic-cooled-until": String(until),
      },
      body: JSON.stringify({ pairs: [] }),
    });
    // Sibling session on the same IP sees the cooldown in its envelope…
    const sib = await (
      await fetch(`${base}/api/lookup`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-ic-session": "laptop2",
          "x-ic-state": "idle",
        },
        body: JSON.stringify({ pairs: [] }),
      })
    ).json();
    assert.equal(sib.hive.cooledUntil, until);
    // …and is refused bounties.
    const take = await (
      await fetch(`${base}/api/bounties`, {
        headers: { "x-ic-session": "laptop2", "x-ic-state": "idle" },
      })
    ).json();
    assert.equal(take.reason, "cooled");
  } finally {
    server.close();
  }
});

test("peers envelope counts spending sessions", async () => {
  _resetForTests();
  const server = makeServer();
  await new Promise((resolve) => server.listen(0, resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const ping = (session, state) =>
    fetch(`${base}/api/lookup`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-ic-session": session,
        "x-ic-state": state,
      },
      body: JSON.stringify({ pairs: [] }),
    }).then((r) => r.json());
  try {
    await ping("a", "running");
    await ping("b", "serving");
    await ping("c", "idle");
    const last = await ping("d", "running");
    // a, b, d spend; c idles.
    assert.equal(last.hive.peers, 3);
  } finally {
    server.close();
  }
});


test("stats metrics + dashboard render", async () => {
  _resetForTests();
  const server = makeServer();
  await new Promise((resolve) => server.listen(0, resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const H = { "Content-Type": "application/json", "x-ic-session": "s1", "x-ic-state": "idle" };
  try {
    await fetch(`${base}/api/contribute`, { method:"POST", headers:H, body: JSON.stringify({ entries: [["Fire","Water","Steam",""]] }) });
    // one hit, one miss
    await fetch(`${base}/api/lookup`, { method:"POST", headers:H, body: JSON.stringify({ pairs: [["Water","Fire"],["No","Hit"]] }) });
    const s = await (await fetch(`${base}/api/stats`)).json();
    assert.equal(s.metrics.lookupPairs, 2);
    assert.equal(s.metrics.lookupHits, 1);
    assert.equal(s.metrics.hitRate, 0.5);
    assert.equal(s.metrics.contributed, 1);
    assert.equal(s.live.sessions, 1);
    const dash = await fetch(`${base}/api/dashboard`);
    assert.equal(dash.headers.get("content-type").split(";")[0], "text/html");
    const html = await dash.text();
    assert.ok(html.includes("Infinite Craft hive"));
    assert.ok(html.includes("50.0%"));
  } finally {
    server.close();
  }
});

test("peer review requires an independent session (R4)", async () => {
  _resetForTests();
  const server = makeServer();
  await new Promise((resolve) => server.listen(0, resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const H = (s) => ({ "Content-Type": "application/json", "x-ic-session": s, "x-ic-state": "idle" });
  const contribute = (s, entries) =>
    fetch(`${base}/api/contribute`, { method: "POST", headers: H(s), body: JSON.stringify({ entries }) }).then((r) => r.json());
  try {
    // Same session contributes the same result twice (even the duplicate-in-
    // one-request attack) → must NOT reach reviewed.
    await contribute("attacker", [["Fire", "Water", "Poison"], ["Fire", "Water", "Poison"]]);
    let s = await (await fetch(`${base}/api/stats`)).json();
    assert.equal(s.reviewed, 0, "same-session duplicate must not self-review");
    await contribute("attacker", [["Fire", "Water", "Poison"]]);
    s = await (await fetch(`${base}/api/stats`)).json();
    assert.equal(s.reviewed, 0, "same session again must still not review");
    // A genuinely independent session confirms → now reviewed.
    await contribute("honest", [["Fire", "Water", "Poison"]]);
    s = await (await fetch(`${base}/api/stats`)).json();
    assert.equal(s.reviewed, 1);
  } finally {
    server.close();
  }
});

test("no snapshot backend → no dirty/deleted accumulation (R6)", async () => {
  _resetForTests();
  const server = makeServer();
  await new Promise((resolve) => server.listen(0, resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const H = { "Content-Type": "application/json", "x-ic-session": "s", "x-ic-state": "idle" };
  try {
    // snapshot.enabled is false (no Upstash env). Contribute + heal must not
    // grow the flush queues, or pendingFlush leaks forever on the free tier.
    await fetch(`${base}/api/contribute`, { method: "POST", headers: H, body: JSON.stringify({ entries: [["Fire", "Water", "Steam"]] }) });
    await fetch(`${base}/api/contribute`, { method: "POST", headers: { ...H, "x-ic-session": "s2" }, body: JSON.stringify({ entries: [["Fire", "Water", "Poison"]] }) }); // conflict → heal
    const s = await (await fetch(`${base}/api/stats`)).json();
    assert.equal(s.snapshot.enabled, false);
    assert.equal(s.snapshot.pendingFlush, 0);
  } finally {
    server.close();
  }
});

test("review bounties are counted in reviewsTaken (metrics undercount fix)", async () => {
  _resetForTests();
  const server = makeServer();
  await new Promise((resolve) => server.listen(0, resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const H = (s, st) => ({ "Content-Type": "application/json", "x-ic-session": s, "x-ic-state": st });
  try {
    // Seed one unreviewed entry → it becomes a review bounty. No pair bounties.
    await fetch(`${base}/api/contribute`, { method: "POST", headers: H("seed", "idle"), body: JSON.stringify({ entries: [["Fire", "Water", "Steam"]] }) });
    let s = await (await fetch(`${base}/api/stats`)).json();
    assert.equal(s.metrics.bountiesTaken, 0);
    assert.equal(s.metrics.reviewsTaken, 0);
    // An idle client polls → gets the review bounty (no pair bounties exist).
    const take = await (await fetch(`${base}/api/bounties?limit=5`, { headers: H("helper", "idle") })).json();
    assert.equal(take.bounties.length, 1);
    assert.equal(take.bounties[0].kind, "review");
    s = await (await fetch(`${base}/api/stats`)).json();
    assert.equal(s.metrics.bountiesTaken, 0, "no pair bounty was served");
    assert.equal(s.metrics.reviewsTaken, 1, "the review handout is now counted");
    // Dashboard surfaces it.
    const html = await (await fetch(`${base}/api/dashboard`)).text();
    assert.ok(html.includes("Reviews served"));
  } finally {
    server.close();
  }
});

test("lease renewal bumps at only; claim survives renewal", () => {
  _resetForTests();
  const t0 = 1_000_000;
  const post = doPostBounties([["Lease", "Me"]], t0, "1.1.1.1", "s1", true);
  assert.equal(post.posted, 1);
  assert.equal(post.renewed, 0);
  const firstAt = _bountyForTests("Lease", "Me").firstAt;
  assert.equal(firstAt, t0);

  const t1 = t0 + 5_000;
  const renew = doPostBounties([["Lease", "Me"]], t1, "1.1.1.1", "s1", true);
  assert.equal(renew.renewed, 1);
  assert.equal(renew.posted, 0);
  const b = _bountyForTests("Lease", "Me");
  assert.equal(b.at, t1);
  assert.equal(b.firstAt, firstAt);

  // Claim, then renew again — claimedBy/claimedAt must stick.
  doTakeBounties(5, "taker", { cooledUntil: 0, running: 0 }, t1, "2.2.2.2");
  assert.equal(_bountyForTests("Lease", "Me").claimedBy, "taker");
  const claimedAt = _bountyForTests("Lease", "Me").claimedAt;
  const t2 = t1 + 1_000;
  doPostBounties([["Lease", "Me"]], t2, "1.1.1.1", "s1", true);
  const after = _bountyForTests("Lease", "Me");
  assert.equal(after.claimedBy, "taker");
  assert.equal(after.claimedAt, claimedAt);
  assert.equal(after.at, t2);
  assert.equal(after.firstAt, firstAt);
});

test("leased vs legacy TTL", () => {
  _resetForTests();
  const leaseTtl = Number(bountyLeaseTtlMs());
  const legacyTtl = Number(bountyLegacyTtlMs());
  assert.equal(leaseTtl, 10_000);
  assert.equal(legacyTtl, 900_000);

  const t0 = 2_000_000;
  doPostBounties([["Leased", "Pair"]], t0, "1.1.1.1", "sL", true);
  pruneBounties(t0 + leaseTtl + 1);
  assert.equal(_bountyForTests("Leased", "Pair"), undefined);

  _resetForTests();
  doPostBounties([["Legacy", "Pair"]], t0, "1.1.1.1", "sG", false);
  pruneBounties(t0 + leaseTtl + 1);
  assert.ok(_bountyForTests("Legacy", "Pair"), "legacy survives past lease TTL");
  pruneBounties(t0 + legacyTtl + 1);
  assert.equal(_bountyForTests("Legacy", "Pair"), undefined);
});

test("lease flag sticks on renewal", () => {
  _resetForTests();
  const leaseTtl = Number(bountyLeaseTtlMs());
  const t0 = 3_000_000;

  // Legacy first, then re-post with lease:true — stays legacy.
  doPostBounties([["Sticky", "A"]], t0, "1.1.1.1", "s1", false);
  doPostBounties([["Sticky", "A"]], t0 + 1_000, "1.1.1.1", "s1", true);
  assert.equal(_bountyForTests("Sticky", "A").leased, false);
  pruneBounties(t0 + 1_000 + leaseTtl + 1);
  assert.ok(_bountyForTests("Sticky", "A"), "legacy TTL still applies after leased re-post");

  // Leased first, then re-post without lease — stays leased.
  _resetForTests();
  doPostBounties([["Sticky", "B"]], t0, "1.1.1.1", "s1", true);
  doPostBounties([["Sticky", "B"]], t0 + 1_000, "1.1.1.1", "s1", false);
  assert.equal(_bountyForTests("Sticky", "B").leased, true);
  pruneBounties(t0 + 1_000 + leaseTtl + 1);
  assert.equal(_bountyForTests("Sticky", "B"), undefined);
});

test("per-session bounty quota; renewals exempt; other sessions unaffected", () => {
  _resetForTests();
  const quota = Number(bountySessionQuota());
  assert.equal(quota, 600);
  const now = 4_000_000;
  const mk = (i) => [`Q${i}`, `R${i}`];

  // MAX_POST is 500 — fill quota across two calls.
  let posted = 0;
  for (let start = 0; start < quota; start += 500) {
    const batch = [];
    for (let i = start; i < Math.min(start + 500, quota); i++) batch.push(mk(i));
    const r = doPostBounties(batch, now, "1.1.1.1", "quota-s");
    posted += r.posted;
  }
  assert.equal(posted, quota);
  assert.equal(doPostBounties([mk(0)], now, "1.1.1.1", "quota-s").open, quota);

  const over = doPostBounties([mk(quota)], now, "1.1.1.1", "quota-s");
  assert.equal(over.posted, 0);
  assert.equal(over.renewed, 0);
  assert.equal(over.open, quota);
  assert.equal(_bountyForTests(...mk(quota)), undefined);

  // Renewals still succeed at quota.
  const renew = doPostBounties([mk(0)], now + 1, "1.1.1.1", "quota-s");
  assert.equal(renew.renewed, 1);
  assert.equal(renew.posted, 0);

  // A different session can still post.
  const other = doPostBounties([["Other", "Session"]], now, "9.9.9.9", "other-s");
  assert.equal(other.posted, 1);
});

test("take handout is round-robin across poster sessions", () => {
  _resetForTests();
  const now = 5_000_000;
  doPostBounties(
    [
      ["Aa1", "Xa"],
      ["Aa2", "Xa"],
      ["Aa3", "Xa"],
    ],
    now,
    "1.1.1.1",
    "A"
  );
  doPostBounties(
    [
      ["Bb1", "Xb"],
      ["Bb2", "Xb"],
    ],
    now + 1,
    "2.2.2.2",
    "B"
  );
  const take = doTakeBounties(4, "taker", { cooledUntil: 0, running: 0 }, now + 2, "3.3.3.3");
  assert.deepEqual(
    take.bounties.map((b) => b.first),
    ["Aa1", "Bb1", "Aa2", "Bb2"]
  );
});

test("pollMs hints for eligible / empty / ip-active", () => {
  _resetForTests();
  const now = 6_000_000;
  const idle = { cooledUntil: 0, running: 0 };

  // Empty board → slow poll.
  const empty = doTakeBounties(5, "t", idle, now, "8.8.8.8");
  assert.equal(empty.pollMs, Number(bountyPollHintMs(0, true)));
  assert.equal(empty.pollMs, 10_000);

  doPostBounties([["Open", "Board"]], now, "1.1.1.1", "poster");
  const open = doTakeBounties(5, "t", idle, now, "8.8.8.8");
  assert.equal(open.bounties.length, 1);
  assert.equal(open.pollMs, Number(bountyPollHintMs(1, true)));
  assert.equal(open.pollMs, 2_000);

  // Refused while IP has a running session — hint is the idle interval.
  doPostBounties([["Still", "Open"]], now, "1.1.1.1", "poster2");
  const busy = doTakeBounties(5, "t", { cooledUntil: 0, running: 1 }, now, "8.8.8.8");
  assert.equal(busy.reason, "ip-active");
  assert.equal(busy.bounties.length, 0);
  assert.equal(busy.pollMs, Number(bountyPollHintMs(0, false)));
  assert.equal(busy.pollMs, 10_000);
});

test("HTTP POST /api/bounties threads lease + session into renewals", async () => {
  _resetForTests();
  const server = makeServer();
  await new Promise((resolve) => server.listen(0, resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const headers = {
    "Content-Type": "application/json",
    "x-ic-session": "http-lease-s",
    "x-ic-state": "running",
    "x-forwarded-for": "5.5.5.5",
  };
  try {
    const first = await (
      await fetch(`${base}/api/bounties`, {
        method: "POST",
        headers,
        body: JSON.stringify({ pairs: [["Http", "Lease"]], lease: true }),
      })
    ).json();
    assert.equal(first.posted, 1);
    assert.equal(first.renewed, 0);
    assert.equal(_bountyForTests("Http", "Lease").leased, true);
    assert.equal(_bountyForTests("Http", "Lease").session, "http-lease-s");

    const second = await (
      await fetch(`${base}/api/bounties`, {
        method: "POST",
        headers,
        body: JSON.stringify({ pairs: [["Http", "Lease"]], lease: true }),
      })
    ).json();
    assert.equal(second.renewed, 1);
    assert.equal(second.posted, 0);
  } finally {
    server.close();
  }
});
