import assert from "node:assert/strict";
import {
  element_matches_pattern,
  match_elements_boundary,
  trace_recipe_boundary,
  export_elements_boundary,
  get_by_name_boundary,
  resolve_element_boundary,
} from "./_sudo/craft.mjs";
import { glob_match } from "./_sudo/regex.mjs";

let passed = 0;
let failed = 0;

function check(name, fn) {
  try {
    fn();
    console.log(`PASS  ${name}`);
    passed++;
  } catch (err) {
    console.log(`FAIL  ${name}`);
    console.log(`      ${err && err.message ? err.message : String(err)}`);
    failed++;
  }
}

check("Real alternation", () => {
  let [matched, error] = element_matches_pattern("Watchdog", "/cat|dog/");
  assert.equal(error, null);
  assert.equal(matched, true);

  [matched, error] = element_matches_pattern("Elephant", "/cat|dog/");
  assert.equal(error, null);
  assert.equal(matched, false);
});

check("Backslash escape \\d", () => {
  let [matched, error] = element_matches_pattern("Area 51", "/\\d+/");
  assert.equal(error, null);
  assert.equal(matched, true);

  [matched, error] = element_matches_pattern("AreaX", "/\\d+/");
  assert.equal(error, null);
  assert.equal(matched, false);
});

check("Glob character classes", () => {
  assert.equal(glob_match("a[bc]d", "abd", true), true);
  assert.equal(glob_match("a[bc]d", "acd", true), true);
  assert.equal(glob_match("a[bc]d", "aed", true), false);
});

check("! exclude filter", () => {
  const elements = [
    ["Water", "💧", false],
    ["Fire", "🔥", false],
    ["Wind", "🌬️", false],
    ["Earth", "🌍", false],
    ["Firewall", "🧱", true],
  ];
  const [matches, error] = match_elements_boundary(elements, "!fire*");
  assert.equal(error, null);
  const names = matches.map((t) => t[0]);
  assert.ok(names.includes("Water"));
  assert.ok(names.includes("Wind"));
  assert.ok(names.includes("Earth"));
  assert.ok(!names.includes("Fire"));
  assert.ok(!names.includes("Firewall"));
});

check("^ first-discovery filter through shared matcher", () => {
  const elements = [
    ["Water", "", false],
    ["Fire", "", true],
    ["Steam", "", true],
  ];
  const [allFirst, err1] = match_elements_boundary(elements, "^");
  assert.equal(err1, null);
  assert.deepEqual(allFirst, [
    ["Fire", "", true],
    ["Steam", "", true],
  ]);

  const [steamOnly, err2] = match_elements_boundary(elements, "^ea");
  assert.equal(err2, null);
  assert.deepEqual(steamOnly, [["Steam", "", true]]);
});

check(">200-layer trace succeeds (unbounded BFS)", () => {
  const recipes = {};
  recipes["C0"] = [["Fire", "Water"]];
  for (let i = 1; i <= 250; i++) {
    recipes["C" + i] = [["C" + (i - 1), "Water"]];
  }
  const elements = [];
  for (let i = 0; i <= 250; i++) {
    elements.push(["C" + i, "", false]);
  }
  // Bases needed by the kernel for reachability
  elements.push(["Fire", "", false]);
  elements.push(["Water", "", false]);

  const [status, , steps] = trace_recipe_boundary(elements, recipes, "C250");
  assert.equal(status, 4);
  assert.equal(steps.length, 251);
});

check("Export closure excludes a pure orphan", () => {
  const elements = [
    ["Water", "", false],
    ["Steam", "", false],
    ["Mystery Gas", "", false],
    ["Orphan", "", false],
  ];
  const recipes = { Steam: [["Mystery Gas", "Water"]] };
  const exported = export_elements_boundary(elements, recipes);
  assert.equal(exported.length, 3);
  const names = exported.map((t) => t[0]);
  assert.ok(names.includes("Water"));
  assert.ok(names.includes("Steam"));
  assert.ok(names.includes("Mystery Gas"));
  assert.ok(!names.includes("Orphan"));
});

check("Exact-case lookup with title-case fallback", () => {
  const elements = [["Fire", "🔥", false]];

  assert.equal(get_by_name_boundary(elements, "fire"), null);
  assert.deepEqual(get_by_name_boundary(elements, "Fire"), ["Fire", "🔥", false]);

  assert.deepEqual(resolve_element_boundary(elements, "fire"), [
    "Fire",
    "🔥",
    false,
  ]);
  assert.deepEqual(resolve_element_boundary(elements, "unicorn dust"), [
    "Unicorn Dust",
    "",
    false,
  ]);
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
