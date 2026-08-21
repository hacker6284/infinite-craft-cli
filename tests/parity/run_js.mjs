#!/usr/bin/env node
/**
 * Host-parity runner: drive JS wiring (trainer.src.mjs) against fixtures.json.
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  matchElements,
  resolveElement,
  recordRecipe,
  traceRecipeCore,
} from "../../bookmarklet/trainer.src.mjs";
// Pure command helpers: the trainer imports these from the kernel unchanged,
// so driving the kernel adapter directly exercises the same code path.
import {
  classify_command_line,
  validate_command_line,
  validate_command_line_segments,
  script_parse,
  script_ast_repr,
  permute_pairs_boundary,
  cross_pairs_boundary,
  with_pairs_boundary,
  unfilled_names_boundary,
  crawl_generation_pairs_boundary,
  prioritize_pairs_boundary,
  sanitize_element_name,
  ic_save_to_batches,
  lineage_steps_to_batches,
  build_export_items_boundary,
  export_elements_boundary,
} from "../../bookmarklet/_sudo/craft.mjs";

// Parity-only helpers: trainer installs seeders on globalThis when loaded
// under Node (not exported on the production API surface).
const _parity = globalThis.__IC_TRAINER_PARITY__;
if (!_parity || typeof _parity.resetState !== "function" || typeof _parity.getRecipeIndex !== "function") {
  throw new Error(
    "trainer parity hooks missing; expected Node install of globalThis.__IC_TRAINER_PARITY__"
  );
}
function _resetStateForParity(elements, recipes) {
  _parity.resetState(elements, recipes);
}
function _getRecipeIndexForParity() {
  return _parity.getRecipeIndex();
}

const __dirname = dirname(fileURLToPath(import.meta.url));
const FIXTURES_PATH = join(__dirname, "fixtures.json");

function canonicalize(value) {
  if (Array.isArray(value)) {
    return value.map(canonicalize);
  }
  if (value !== null && typeof value === "object") {
    const out = {};
    for (const key of Object.keys(value).sort()) {
      out[key] = canonicalize(value[key]);
    }
    return out;
  }
  return value;
}

function sortPairs(pairs) {
  return pairs.slice().sort((x, y) =>
    x[0] < y[0] ? -1 : x[0] > y[0] ? 1 : x[1] < y[1] ? -1 : x[1] > y[1] ? 1 : 0
  );
}

function resolveElementsAndRecipes(scenario, fixtures) {
  if (scenario.chain != null) {
    const N = scenario.chain;
    const elements = [
      ["Fire", "", false],
      ["Water", "", false],
    ];
    const recipes = { C0: [["Fire", "Water"]] };
    for (let i = 0; i <= N; i++) elements.push([`C${i}`, "", false]);
    for (let i = 1; i <= N; i++) recipes[`C${i}`] = [[`C${i - 1}`, "Water"]];
    return { elements, recipes };
  }

  let elements;
  if (scenario.elements_set) {
    elements = fixtures.element_sets[scenario.elements_set];
  } else if (scenario.elements) {
    elements = scenario.elements;
  } else {
    elements = [];
  }

  const recipes = scenario.recipes || scenario.initial_recipes || {};
  return { elements, recipes };
}

function runScenario(scenario, fixtures) {
  const op = scenario.op;

  if (op === "record_recipe") {
    _resetStateForParity([], scenario.initial_recipes || {});
    for (const call of scenario.calls) {
      recordRecipe(call.result, call.a, call.b);
    }
    const loaded = _getRecipeIndexForParity();
    const out = {};
    for (const [k, pairs] of Object.entries(loaded)) {
      out[k] = sortPairs(pairs.map(([a, b]) => [a, b]));
    }
    return out;
  }

  const { elements, recipes } = resolveElementsAndRecipes(scenario, fixtures);
  _resetStateForParity(elements, recipes);

  if (op === "match") {
    const { matches } = matchElements(scenario.query);
    return matches.map((e) => [e.text, e.emoji || "", !!e.discovered]);
  }

  if (op === "resolve") {
    const e = resolveElement(scenario.name);
    return [e.text, e.emoji || "", !!e.discovered];
  }

  if (op === "trace") {
    const r = traceRecipeCore(scenario.name);
    return {
      status: r.status,
      target: r.target,
      steps: r.steps.map(([a, b, res]) => [a, b, res]),
    };
  }

  if (op === "export") {
    const included = export_elements_boundary(elements, recipes);
    return included
      .map(([n, em, f]) => [n, em || "", !!f])
      .sort((x, y) => (x[0] < y[0] ? -1 : x[0] > y[0] ? 1 : 0));
  }

  if (op === "classify") {
    const r = classify_command_line(scenario.line);
    return r === null || r === undefined ? null : Array.from(r);
  }

  if (op === "validate") {
    const r = validate_command_line(scenario.line);
    return r === undefined ? null : r;
  }

  if (op === "script_parse") {
    const [ok, nodes, kids, muts, err] = script_parse(scenario.source);
    if (!ok) return ["err", err];
    return ["ok", script_ast_repr(nodes, kids, nodes.length - 1), muts.map(Boolean)];
  }

  if (op === "permute_pairs") {
    return permute_pairs_boundary(scenario.matches).map((t) => Array.from(t));
  }

  if (op === "cross_pairs") {
    return cross_pairs_boundary(scenario.left, scenario.right).map((t) =>
      Array.from(t)
    );
  }

  if (op === "with_pairs") {
    return with_pairs_boundary(scenario.target, scenario.others).map((t) =>
      Array.from(t)
    );
  }

  if (op === "unfilled") {
    return Array.from(unfilled_names_boundary(elements, recipes));
  }

  if (op === "sanitize") {
    return sanitize_element_name(scenario.name);
  }

  if (op === "ic_batches") {
    const [els, recs] = ic_save_to_batches(
      scenario.items,
      scenario.recipe_refs || []
    );
    return {
      elements: els.map((t) => Array.from(t)),
      recipes: recs.map((t) => Array.from(t)),
    };
  }

  if (op === "lineage_batches") {
    const [els, recs] = lineage_steps_to_batches(scenario.steps);
    return {
      elements: els.map((t) => Array.from(t)),
      recipes: recs.map((t) => Array.from(t)),
    };
  }

  if (op === "export_items") {
    const [items, refs] = build_export_items_boundary(elements, recipes);
    return {
      items: items.map((t) => Array.from(t)),
      refs: refs.map((t) => Array.from(t)),
    };
  }

  if (op === "prioritize_pairs") {
    return prioritize_pairs_boundary(
      scenario.pairs,
      recipes,
      scenario.cached || []
    ).map((t) => Array.from(t));
  }

  if (op === "crawl_pairs") {
    const [rawPairs, newKeys] = crawl_generation_pairs_boundary(
      scenario.pool,
      scenario.tried_keys || []
    );
    return {
      pairs: rawPairs.map((t) => Array.from(t)),
      new_keys: Array.from(newKeys),
    };
  }

  if (op === "validate_segments") {
    const segs = validate_command_line_segments(scenario.line);
    if (segs === null || segs === undefined) return null;
    return segs.map(([text, hl]) => [text, !!hl]);
  }

  throw new Error(`unknown op: ${op}`);
}

function main() {
  const fixtures = JSON.parse(readFileSync(FIXTURES_PATH, "utf8"));
  const results = {};

  for (const scenario of fixtures.scenarios) {
    const sid = scenario.id;
    try {
      results[sid] = runScenario(scenario, fixtures);
    } catch (err) {
      console.error(`ERROR in scenario ${JSON.stringify(sid)}:`, err);
      process.exit(1);
    }
  }

  console.log(JSON.stringify(canonicalize(results), null, 2));
  process.exit(0);
}

main();
