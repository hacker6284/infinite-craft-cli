import assert from "node:assert/strict";
import {
  glob_match,
  glob_match_js,
  match_elements_boundary,
  match_elements_js_boundary,
  trace_recipe_boundary,
  trace_recipe_js_boundary,
  export_elements_boundary,
  export_elements_js_boundary,
} from "./_sudo/craft.mjs";

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

check("Glob character classes work", () => {
  assert.equal(glob_match("a[bc]d", "abd"), true);
  assert.equal(glob_match("a[bc]d", "acd"), true);
  assert.equal(glob_match("a[bc]d", "aed"), false);
  // Old JS: brackets fall through to literal chars
  assert.equal(glob_match_js("a[bc]d", "abd"), false);
  assert.equal(glob_match_js("a[bc]d", "a[bc]d"), true);
});

check("^ filtering applies through the shared matcher", () => {
  const elements = [
    ["Water", "", false],
    ["Fire", "", true],
    ["Steam", "", true],
  ];
  const allFirst = match_elements_boundary(elements, "^");
  assert.deepEqual(allFirst, [
    ["Fire", "", true],
    ["Steam", "", true],
  ]);
  const steamOnly = match_elements_boundary(elements, "^ea");
  assert.deepEqual(steamOnly, [["Steam", "", true]]);
  // Old JS treats ^ as a literal outside doSearch
  assert.deepEqual(match_elements_js_boundary(elements, "^ea"), []);
});

check("Recipe lineage deeper than 200 layers traces successfully", () => {
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

  const [jsStatus] = trace_recipe_js_boundary(elements, recipes, "C250");
  assert.equal(jsStatus, 3); // Unreachable — old 200-layer cap
});

check("Export closure excludes an orphan", () => {
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

  const jsExported = export_elements_js_boundary(elements);
  assert.equal(jsExported.length, 4);
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
