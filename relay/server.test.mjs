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
  _resetForTests,
} from "./server.mjs";

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
  const H = (session, state) => ({
    "Content-Type": "application/json",
    "x-ic-session": session,
    "x-ic-state": state,
  });
  try {
    // Seed one cached pair so posting returns it as a result, not a bounty.
    doContribute([["Fire", "Water", "Steam", ""]]);
    // Poster (running) posts three pairs; one is already cached.
    const post = await (
      await fetch(`${base}/api/bounties`, {
        method: "POST",
        headers: H("poster", "running"),
        body: JSON.stringify({ pairs: [["Water", "Fire"], ["Earth", "Wind"], ["Lava", "Sea"]] }),
      })
    ).json();
    assert.equal(post.posted, 2);
    assert.equal(Object.keys(post.results).length, 1);

    // Same-IP taker is refused while the poster is running (127.0.0.1 shared).
    const refused = await (
      await fetch(`${base}/api/bounties?limit=5`, { headers: H("helper", "idle") })
    ).json();
    assert.equal(refused.bounties.length, 0);
    assert.equal(refused.reason, "ip-active");
    assert.equal(refused.hive.peers, 1);

    // Poster goes idle → helper is eligible and claims both bounties
    // (plus the review bounty for the seeded unreviewed entry).
    await fetch(`${base}/api/lookup`, {
      method: "POST",
      headers: H("poster", "idle"),
      body: JSON.stringify({ pairs: [] }),
    });
    const take = await (
      await fetch(`${base}/api/bounties?limit=5`, { headers: H("helper", "serving") })
    ).json();
    const kinds = take.bounties.map((b) => b.kind).sort();
    assert.deepEqual(kinds, ["pair", "pair", "review"]);
    const review = take.bounties.find((b) => b.kind === "review");
    assert.equal(review.first, "Fire");
    assert.equal(review.second, "Water");

    // Claimed bounties are not re-offered inside the claim TTL.
    const again = await (
      await fetch(`${base}/api/bounties?limit=5`, { headers: H("helper2", "idle") })
    ).json();
    assert.equal(again.bounties.length, 0);

    // Helper fulfills one pair bounty + confirms the review → entry reviewed.
    const fulfil = await (
      await fetch(`${base}/api/contribute`, {
        method: "POST",
        headers: H("helper", "serving"),
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
