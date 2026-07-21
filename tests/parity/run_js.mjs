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
  exportIncludedCore,
  _resetStateForParity,
  _getRecipeIndexForParity,
} from "../../bookmarklet/trainer.src.mjs";
// Pure command helpers: the trainer imports these from the kernel unchanged,
// so driving the kernel adapter directly exercises the same code path.
import {
  classify_command_line,
  validate_command_line,
} from "../../bookmarklet/_sudo/craft.mjs";

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
    const included = exportIncludedCore();
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
