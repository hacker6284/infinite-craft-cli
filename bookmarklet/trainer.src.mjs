// Single source of truth for the trainer's UI and effects. The pure
// kernel (matching, resolution, recipes, lineage, export) lives in
// ../sudo/craft.sudo and is imported below as generated code, built by
// Bazel's //bookmarklet:_sudo target. trainer.js / trainer.min.js are
// build-only outputs of //bookmarklet:trainer_js / :trainer_min_js; do not
// edit them by hand (they are not committed to the repo).
import {
  match_elements_boundary as matchElementsBoundary,
  resolve_element_boundary as resolveElementBoundary,
  add_element_boundary as addElementBoundary,
  add_elements_batch_boundary as addElementsBatchBoundary,
  record_recipe as recordRecipeKernel,
  record_recipes_batch as recordRecipesBatchKernel,
  trace_recipe_boundary as traceRecipeBoundary,
  orphan_candidates_boundary as orphanCandidatesBoundary,
  exhaust_pairs_boundary as exhaustPairsBoundary,
  permute_pairs_boundary as permutePairsBoundary,
  cross_pairs_boundary as crossPairsBoundary,
  with_pairs_boundary as withPairsBoundary,
  crawl_generation_pairs_boundary as crawlGenerationPairsBoundary,
  prioritize_pairs_boundary as prioritizePairsBoundary,
  ic_save_to_batches as icSaveToBatches,
  lineage_steps_to_batches as lineageStepsToBatches,
  build_export_items_boundary as buildExportItemsBoundary,
  pair_key as pairKeyKernel,
  is_base_element as isBaseElement,
  sanitize_element_name as sanitizeElementName,
  unfilled_names_boundary as unfilledNamesBoundary,
  is_local_command as isLocalCommand,
  command_queue_lane as commandQueueLane,
  queue_accept as queueAccept,
  queue_lane_busy as queueLaneBusy,
  lane_should_reset_cancel as laneShouldResetCancel,
  parse_target_arg as parseTargetArg,
  target_outcome as targetOutcome,
  is_target_hit as isTargetHitKernel,
  confirm_should_continue as confirmShouldContinue,
  is_confirm_answer as isConfirmAnswer,
  confirm_answer_key as confirmAnswerKey,
  should_bulk_warn as shouldBulkWarn,
  fetch_timeout_ms as fetchTimeoutMs,
  pair_should_retry as pairShouldRetry,
  pair_retry_backoff_ms as pairRetryBackoffMs,
  ib_should_retry as ibShouldRetry,
  ib_retry_backoff_ms as ibRetryBackoffMs,
  rate_slots_left as rateSlotsLeft,
  rate_next_slot_frac_milli as rateNextSlotFracMilli,
  rate_bar_fills as rateBarFills,
  rate_bar_segment as rateBarSegment,
  rate_format_pair_for_width as rateFormatPairForWidth,
  classify_command_line as classifyCommandLine,
  validate_command_line_segments as validateCommandLineSegments,
  slash_args as slashArgs,
  parse_two_elements as parseTwoElements,
  parse_operands as parseOperands,
  parse_with_args as parseWithArgs,
  parse_cross_queries as parseCrossQueries,
} from "./_sudo/craft.mjs";
import { TRAINER_VERSION } from "./version.mjs";

// ── Constants ────────────────────────────────────────────────────────
const RATE_LIMIT = 60;
const RATE_WINDOW = 60000;
const BULK_WARN = 200;
const MAX_QUEUE_DEPTH = 50;
const MAX_PERMUTATE_ROUNDS = 50;

// ── State ────────────────────────────────────────────────────────────
const history = []; // [{a, b, result}]
const pairCache = new Map(); // "A\0B" -> {text,emoji,discovered}
const cmdHistory = [];
let cmdHistoryIdx = -1;
let cancelled = false;
let running = false;
let activeRuns = 0; // refcount: pair + IB can run concurrently
let waitingForConfirm = false;
let confirmResolve = null;
let confirmReason = ""; // chrome job-row reason; keys live only on the prompt
// Two independent queues: neal.fun pair API vs Infinibrowser (import/fill/prune).
const pairQueue = [];
const ibQueue = [];
let currentPairCommand = "";
let currentIbCommand = "";
let activeAbort = null;
let pairWorkerRunning = false;
let ibWorkerRunning = false;
// IB job progress (separate from pair jobDone/jobTotal so chrome can dual-run).
let ibJobDone = 0;
let ibJobTotal = 0;
// /target: pause batches when this element name is crafted ("" = no target)
let targetElement = "";
let targetHitChain = Promise.resolve(); // serialize target acks

// DOM refs — initialized in initBrowserUI() so module evaluation is Node-safe
let output, input, body, toggle, stopBtn, queueEl, rateEl, jobEl, promptEl;

// Sticky chrome job state (pair-API budget bar is derived from timestamps)
let jobDone = 0;
let jobTotal = 0;
let lastPairA = null; // text | null — last pair considered this command
let lastPairB = null;
let rateTickerId = null;
const RATE_TICK_MS = 300;
// Segmented rate bar: left 1/2 = next-slot wait refill, right 1/2 = remaining.
const RATE_BAR_LEFT = 9;
const RATE_BAR_RIGHT = 9;


// ── Output helpers ───────────────────────────────────────────────────
function print(html) {
  const div = document.createElement("div");
  div.innerHTML = html;
  output.appendChild(div);
  output.scrollTop = output.scrollHeight;
}
function esc(s) { const d = document.createElement("span"); d.textContent = s; return d.innerHTML; }
// Kernel validation errors arrive as (text, highlight) segment lists; every
// segment is HTML-escaped here, highlighted ones get the yellow span.
function renderErrorSegments(segments) {
  return segments.map(([text, hl]) => (hl ? yellow(esc(text)) : esc(text))).join("");
}
// wrap() inserts html into innerHTML — callers must pass pre-escaped text (via esc()).
function wrap(cls, html) { return `<span class="${cls}">${html}</span>`; }
function bold(t) { return wrap("ict-bold", t); }
function green(t) { return wrap("ict-green", t); }
function yellow(t) { return wrap("ict-yellow", t); }
function cyan(t) { return wrap("ict-cyan", t); }
function magenta(t) { return wrap("ict-magenta", t); }
function red(t) { return wrap("ict-red", t); }
function dim(t) { return wrap("ict-dim", t); }

function formatElement(el) {
  if (!el || !el.text) return dim("Nothing");
  let s = el.emoji ? `${el.emoji} ${esc(el.text)}` : esc(el.text);
  if (el.discovered) s += " " + magenta("[FIRST DISCOVERY!]");
  return s;
}

function formatResult(a, b, result) {
  return `  ${formatElement(a)} + ${formatElement(b)} = ${formatElement(result)}`;
}

// ── Storage layer (IndexedDB) ─────────────────────────────────────────
// The game stores items in IndexedDB "infinite-craft" database, "items" store.
// Each item: {id, saveId, text, emoji, discovered?, recipes?: [[id,id],...]}
// We load everything into memory at startup and write back on mutations.

const DB_NAME = "infinite-craft";
const ITEMS_STORE = "items";
const SAVES_STORE = "saves";
let _items = [];        // in-memory cache (filtered to active save)
let _allItems = [];     // all items across saves (for ID generation)
let _nameIndex = {};    // exact text -> item
let _idIndex = {};      // id -> item
let _nextId = 0;
let _saveId = 0;        // active save ID
let _db = null;

function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function loadAllItems() {
  return new Promise((resolve, reject) => {
    const tx = _db.transaction(ITEMS_STORE, "readonly");
    const store = tx.objectStore(ITEMS_STORE);
    const req = store.getAll();
    req.onsuccess = () => resolve(req.result || []);
    req.onerror = () => reject(req.error);
  });
}

function loadSaves() {
  return new Promise((resolve, reject) => {
    const tx = _db.transaction(SAVES_STORE, "readonly");
    const store = tx.objectStore(SAVES_STORE);
    const req = store.getAll();
    req.onsuccess = () => resolve(req.result || []);
    req.onerror = () => reject(req.error);
  });
}

function detectActiveSave(saves, allItems) {
  // Find the save with the most recent update time
  let active = saves[0];
  for (const s of saves) {
    if (s.updated > active.updated) active = s;
  }
  return active ? active.id : 0;
}

function putItem(item) {
  if (!_db) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const tx = _db.transaction(ITEMS_STORE, "readwrite");
    const store = tx.objectStore(ITEMS_STORE);
    const req = store.put(item);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
  });
}

function deleteItem(id) {
  return new Promise((resolve, reject) => {
    const tx = _db.transaction(ITEMS_STORE, "readwrite");
    const store = tx.objectStore(ITEMS_STORE);
    const req = store.delete(id);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
  });
}

function rebuildIndexes() {
  _nameIndex = {};
  _idIndex = {};
  _nextId = 0;
  // _nextId must account for ALL items across saves to avoid ID collisions
  for (const item of _allItems) {
    if (item.id >= _nextId) _nextId = item.id + 1;
  }
  // But name/id indexes only cover the active save
  for (const item of _items) {
    _nameIndex[item.text] = item;
    _idIndex[item.id] = item;
  }
}

function getAllElements() {
  return _items;
}

function getByName(name) {
  return _nameIndex[name] || null;
}

function toTuples(elements) {
  return elements.map(e => [e.text, e.emoji || "", !!e.discovered]);
}

function elementTuples() {
  return toTuples(getAllElements());
}

function pairsFromBoundary(rawPairs) {
  return rawPairs.map(([at, ae, af, bt, be, bf]) => [
    { text: at, emoji: ae, discovered: af },
    { text: bt, emoji: be, discovered: bf },
  ]);
}

function resolveElement(name) {
  const [text, emoji, discovered] = resolveElementBoundary(elementTuples(), name);
  return { text, emoji, discovered };
}

// ── Live page sync ───────────────────────────────────────────────────
// The game's addAPI hook exposes window.IC; IC.getItems() returns the
// reactive Vue array behind the sidebar, so pushing/splicing there updates
// the open page without a refresh. The page does NOT persist items added
// this way — IndexedDB writes stay on our side. Best-effort: if neal.fun
// renames the hook the trainer still works (refresh to see changes).
function livePageItems() {
  try {
    const ic = typeof window !== "undefined" ? window.IC : null;
    if (!ic || typeof ic.getItems !== "function") return null;
    // Never touch the page list while it shows a different save.
    if (typeof ic.getCurrentSave === "function" && ic.getCurrentSave() !== _saveId) return null;
    const arr = ic.getItems();
    return Array.isArray(arr) ? arr : null;
  } catch {
    return null;
  }
}

function pageAddItem(item) {
  const arr = livePageItems();
  if (!arr) return;
  try {
    // The page may already know the element (e.g. crafted by hand after we
    // loaded); match by text so we never show a duplicate sidebar entry.
    if (!arr.some((i) => i && (i.id === item.id || i.text === item.text))) arr.push(item);
  } catch {}
}

function pageRemoveItem(id) {
  const arr = livePageItems();
  if (!arr) return;
  try {
    const idx = arr.findIndex((i) => i && i.id === id);
    if (idx >= 0) arr.splice(idx, 1);
  } catch {}
}

// Reverse direction: adopt elements the player crafts by hand (and follow
// page-side deletions / save resets) so trainer commands see them without a
// reload. Polling stays independent of the page's Vue internals; anything
// exotic that slips through is recoverable via /import. The page persists
// its own items, so adoption indexes the page's live objects — no putItem.
const PAGE_SYNC_MS = 1500;
let pageSyncId = null;
const _pageRecipeCounts = new Map(); // page item id -> recipes.length already folded

function syncFromPage() {
  const arr = livePageItems();
  if (!arr || !arr.length) return;
  const pageIds = new Set();
  const newRecipes = [];
  for (const item of arr) {
    if (!item || typeof item.text !== "string") continue;
    pageIds.add(item.id);
    if (!_idIndex[item.id] && !_nameIndex[item.text]) {
      _items.push(item);
      _allItems.push(item);
      _nameIndex[item.text] = item;
      _idIndex[item.id] = item;
      if (item.id >= _nextId) _nextId = item.id + 1;
    }
  }
  // Fold recipe deltas per item (count-diff); the kernel dedupes, so
  // re-seeing a pair the trainer recorded itself is a no-op.
  for (const item of arr) {
    if (!item || typeof item.text !== "string") continue;
    const recipes = Array.isArray(item.recipes) ? item.recipes : [];
    const seen = _pageRecipeCounts.get(item.id) || 0;
    for (let k = seen; k < recipes.length; k++) {
      const pair = recipes[k];
      if (!Array.isArray(pair) || pair.length !== 2) continue;
      const a = _idIndex[pair[0]], b = _idIndex[pair[1]];
      if (a && b) newRecipes.push([item.text, a.text, b.text]);
    }
    if (recipes.length !== seen) _pageRecipeCounts.set(item.id, recipes.length);
  }
  if (newRecipes.length) recordRecipesBatchKernel(recipeIndex, newRecipes);
  // Adoption makes _items a superset of the page list, so a length mismatch
  // can only mean page-side deletions.
  if (_items.length !== pageIds.size) {
    const gone = _items.filter((i) => !pageIds.has(i.id));
    const goneIds = new Set(gone.map((i) => i.id));
    _items = _items.filter((i) => !goneIds.has(i.id));
    _allItems = _allItems.filter((i) => !goneIds.has(i.id));
    for (const i of gone) {
      delete _nameIndex[i.text];
      delete _idIndex[i.id];
      _pageRecipeCounts.delete(i.id);
    }
  }
}

function startPageSync() {
  if (pageSyncId) clearInterval(pageSyncId);
  // Prime the recipe counts so the first tick doesn't re-fold every recipe
  // rebuildRecipeIndex() already indexed from the same rows.
  const arr = livePageItems();
  if (arr) {
    for (const item of arr) {
      if (item) _pageRecipeCounts.set(item.id, Array.isArray(item.recipes) ? item.recipes.length : 0);
    }
  }
  pageSyncId = setInterval(() => {
    try { syncFromPage(); } catch {}
  }, PAGE_SYNC_MS);
}

function _materializeElement(text, emoji, discovered) {
  const item = { id: _nextId++, saveId: _saveId, text, emoji: emoji || "" };
  if (discovered) item.discovered = true;
  _items.push(item);
  _nameIndex[text] = item;
  _idIndex[item.id] = item;
  putItem(item);
  pageAddItem(item);
}

function addElement(text, emoji, discovered) {
  // Kernel storage normalization + insert-or-ignore decision (same rule as
  // the CLI: no discovered-flag promotion on re-add); the host only
  // materializes the item and persists.
  text = sanitizeElementName(text);
  if (!addElementBoundary(elementTuples(), text, emoji || "", !!discovered)) return false;
  _materializeElement(text, emoji, discovered);
  return true;
}

function addElementsBatch(batch) {
  // Batch variant: one kernel decision pass; appended tuples come back on
  // the inout list and are materialized in order.
  const tuples = elementTuples();
  const before = tuples.length;
  const normalized = batch.map(([text, emoji, discovered]) => [
    sanitizeElementName(text),
    emoji || "",
    !!discovered,
  ]);
  const count = addElementsBatchBoundary(tuples, normalized);
  for (let i = before; i < tuples.length; i++) {
    const [text, emoji, discovered] = tuples[i];
    _materializeElement(text, emoji, discovered);
  }
  return count;
}

async function removeElement(name) {
  const item = _nameIndex[name];
  if (!item || isBaseElement(item.text)) return false;
  _items = _items.filter(i => i.id !== item.id);
  _allItems = _allItems.filter(i => i.id !== item.id);
  delete _nameIndex[item.text];
  delete _idIndex[item.id];
  await deleteItem(item.id);
  pageRemoveItem(item.id);
  return true;
}

// ── Recipe index (name-based, rebuilt from game data) ────────────────
let recipeIndex = {}; // {resultName: [[aName, bName], ...]}

function rebuildRecipeIndex() {
  // ONE kernel call for the whole save. The adapter marshals the entire
  // recipe map in and out per call, so folding pair-by-pair is O(R^2) in
  // marshalling — on a few thousand recipes that held "Loading game
  // data..." for a long time. record_recipes_batch folds every entry
  // inside a single marshal round-trip.
  recipeIndex = {};
  const entries = [];
  for (const item of _items) {
    if (!item.recipes || !item.recipes.length) continue;
    for (const pair of item.recipes) {
      if (pair.length !== 2) continue;
      const a = _idIndex[pair[0]], b = _idIndex[pair[1]];
      if (!a || !b) continue;
      entries.push([item.text, a.text, b.text]);
    }
  }
  if (entries.length) recordRecipesBatchKernel(recipeIndex, entries);
}

function persistRecipe(resultName, aName, bName) {
  // Persist to IndexedDB
  const resultItem = _nameIndex[resultName];
  const aItem = _nameIndex[aName];
  const bItem = _nameIndex[bName];
  if (resultItem && aItem && bItem) {
    if (!resultItem.recipes) resultItem.recipes = [];
    const pair = [aItem.id, bItem.id].sort((x, y) => x - y);
    const has = resultItem.recipes.some(r => r[0] === pair[0] && r[1] === pair[1]);
    if (!has) {
      resultItem.recipes.push(pair);
      putItem(resultItem);
    }
  }
}

function recordRecipe(resultName, aName, bName) {
  recordRecipeKernel(recipeIndex, resultName, aName, bName);
  persistRecipe(resultName, aName, bName);
}

function recordRecipesBatch(entries) {
  if (!entries.length) return;
  recordRecipesBatchKernel(recipeIndex, entries);
  for (const [result, a, b] of entries) persistRecipe(result, a, b);
}

// ── Inventory / recipe seeders (module-private) ──────────────────────
// Used by the Node parity lockstep via a side channel installed below;
// not part of the production named-export API.
function _resetStateForParity(elements, recipes) {
  _items = elements.map(([text, emoji, discovered], i) => {
    const item = { id: i, saveId: 0, text, emoji: emoji || "" };
    if (discovered) item.discovered = true;
    return item;
  });
  _allItems = _items;
  _saveId = 0;
  rebuildIndexes();
  recipeIndex = {};
  for (const [result, pairs] of Object.entries(recipes || {})) {
    recipeIndex[result] = pairs.map(([a, b]) => [a, b]);
  }
}

function _getRecipeIndexForParity() {
  return recipeIndex;
}

// Node parity (tests/parity/run_js.mjs) needs to seed the same module-private
// inventory/recipeIndex the browser host uses. Keep the seeders unexported
// from the production export list; open them only when not in a browser.
if (typeof window === "undefined") {
  globalThis.__IC_TRAINER_PARITY__ = {
    resetState: _resetStateForParity,
    getRecipeIndex: _getRecipeIndexForParity,
  };
}

// ── Rate limiter ─────────────────────────────────────────────────────
const timestamps = [];
// Waits are one timer against a deadline, not a chain of 50ms polls: hidden
// tabs throttle timer chains ≥5 deep to one fire per minute (Chrome
// intensive throttling), which turned a single 60s rate-limit wait — 1200
// chunks — into a ~20-hour stall for anyone running bulk work in a
// background tab. Cancellation rejects the sleepers directly instead of
// relying on the next poll tick.
const activeSleepers = new Set();
function sleepCancellable(ms) {
  return new Promise((resolve, reject) => {
    if (cancelled) { reject(new Error("Cancelled")); return; }
    const deadline = Date.now() + ms;
    const sleeper = { timer: 0, cancel: null };
    function settleReject() {
      clearTimeout(sleeper.timer);
      activeSleepers.delete(sleeper);
      reject(new Error("Cancelled"));
    }
    function fire() {
      if (cancelled) { settleReject(); return; }
      const left = deadline - Date.now();
      if (left <= 0) {
        activeSleepers.delete(sleeper);
        resolve();
        return;
      }
      sleeper.timer = setTimeout(fire, left);
    }
    sleeper.cancel = settleReject;
    activeSleepers.add(sleeper);
    sleeper.timer = setTimeout(fire, ms);
  });
}

/** Reject every in-flight cancellable sleep; call after setting `cancelled`. */
function cancelSleepers() {
  for (const s of [...activeSleepers]) s.cancel();
}

// Time of last slot free (window expiry). Left half resets here and fills
// until the next free — interval-scaled, not always a full 60s.
let lastSlotFreedAt = null;

function pruneRateTimestamps(now = Date.now()) {
  let freed = false;
  while (timestamps.length && timestamps[0] <= now - RATE_WINDOW) {
    timestamps.shift();
    freed = true;
  }
  if (freed) lastSlotFreedAt = now;
}

/**
 * Progress toward next slot free in thousandths [0, 1000] (kernel pure math).
 * Resets when a timestamp drops off; fills over (nextDrop - lastDrop), not the full window.
 */
function nextSlotFracMilli(now = Date.now()) {
  if (!timestamps.length) return 1000;
  return rateNextSlotFracMilli(
    now,
    timestamps[0],
    lastSlotFreedAt == null ? 0 : lastSlotFreedAt,
    RATE_WINDOW,
    true,
    lastSlotFreedAt != null
  );
}

/** @returns {{ remaining: number, max: number, oldestFracMilli: number }} */
function rateChromeSnapshot(now = Date.now()) {
  pruneRateTimestamps(now);
  const remaining = rateSlotsLeft(timestamps.length, RATE_LIMIT);
  return { remaining, max: RATE_LIMIT, oldestFracMilli: nextSlotFracMilli(now) };
}

function acquireRate() {
  return new Promise((resolve, reject) => {
    function tryAcquire() {
      if (cancelled) { reject(new Error("Cancelled")); return; }
      const now = Date.now();
      pruneRateTimestamps(now);
      if (timestamps.length < RATE_LIMIT) {
        timestamps.push(now);
        updateChrome();
        resolve();
      } else {
        const wait = timestamps[0] + RATE_WINDOW - now + 10;
        updateChrome();
        sleepCancellable(wait).then(tryAcquire).catch(reject);
      }
    }
    tryAcquire();
  });
}

// ── API client ───────────────────────────────────────────────────────
// Per-attempt abort: user Stop (activeAbort) or timeout, whichever fires
// first. Without a timeout one stalled request (sleep/wake, network change,
// proxy) hangs a bulk run forever with the job chrome frozen mid-count.
const FETCH_TIMEOUT_MS = fetchTimeoutMs();
function attemptSignal(timeoutMs) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  const outer = activeAbort ? activeAbort.signal : null;
  const onAbort = () => ctl.abort();
  if (outer) {
    if (outer.aborted) ctl.abort();
    else outer.addEventListener("abort", onAbort, { once: true });
  }
  return {
    signal: ctl.signal,
    done() {
      clearTimeout(timer);
      if (outer) outer.removeEventListener("abort", onAbort);
    },
  };
}

function pairKey(a, b) {
  const [ka, kb] = pairKeyKernel(a, b);
  return ka + "\0" + kb;
}

async function apiPair(firstName, secondName) {
  if (cancelled) throw new Error("Cancelled");
  const key = pairKey(firstName, secondName);
  if (pairCache.has(key)) return pairCache.get(key);
  await acquireRate();
  if (cancelled) throw new Error("Cancelled");
  const url = `/api/infinite-craft/pair?first=${encodeURIComponent(firstName)}&second=${encodeURIComponent(secondName)}`;
  let resp;
  let json = null;
  for (let attempt = 0; ; attempt++) {
    if (cancelled) throw new Error("Cancelled");
    const guard = attemptSignal(FETCH_TIMEOUT_MS);
    try {
      resp = await fetch(url, { signal: guard.signal });
      // Body read shares the attempt guard so a stalled body times out too.
      if (resp.ok) { json = await resp.json(); break; }
    } catch (e) { /* retry */ } finally {
      guard.done();
    }
    if (!pairShouldRetry(attempt)) break;
    await sleepCancellable(pairRetryBackoffMs(attempt));
  }
  if (cancelled) throw new Error("Cancelled");
  if (json == null) throw new Error("API request failed");
  let result = null;
  if (json.result && json.result !== "Nothing") {
    result = { text: json.result, emoji: json.emoji || "", discovered: !!json.isNew };
  }
  pairCache.set(key, result);
  return result;
}

// ── Combine single pair ──────────────────────────────────────────────
async function doCombine(aName, bName) {
  const a = resolveElement(aName);
  const b = resolveElement(bName);
  try {
    beginRun();
    setLaneProgress("pair", 0, 1);
    setLastPair(a.text, b.text);
    const result = await apiPair(a.text, b.text);
    setLaneProgress("pair", 1, 1);
    if (cancelled) return;
    if (result) {
      addElement(a.text, a.emoji, false);
      addElement(b.text, b.emoji, false);
      const isNew = addElement(result.text, result.emoji, result.discovered);
      recordRecipe(result.text, a.text, b.text);
      history.push({ a: a.text, b: b.text, result: result.text });
      let extra = isNew ? " " + green("(new)") : "";
      const hit = isTargetHitKernel(targetElement, result.text || "");
      if (hit) extra += " " + bold(yellow("★ TARGET ★"));
      print(formatResult(a, b, result) + extra);
      if (hit) {
        await acknowledgeTargetHit(a.text, b.text, result.text || "");
      }
    } else {
      history.push({ a: a.text, b: b.text, result: "Nothing" });
      print(formatResult(a, b, null));
    }
  } catch (e) {
    if (!cancelled) print("  " + red(`Error: ${esc(e.message)}`));
  } finally {
    endRun();
  }
}

// ── Element matching (kernel-backed) ─────────────────────────────────
function matchElements(query) {
  const [rawMatches, error] = matchElementsBoundary(elementTuples(), query);
  const matches = rawMatches.map(([text, emoji, discovered]) => ({ text, emoji, discovered }));
  return { matches, error };
}

// ── Run state helpers (DOM-facing; assigned stopBtn after init) ───────
// Refcounted so pair work and IB work can run concurrently without one
// endRun() hiding Stop or clearing the other's cancel/abort mid-flight.
function beginRun() {
  if (activeRuns === 0) {
    cancelled = false;
    activeAbort = new AbortController();
  }
  activeRuns++;
  running = true;
  try { stopBtn.style.display = "inline"; } catch {}
}

function endRun() {
  activeRuns = Math.max(0, activeRuns - 1);
  if (activeRuns === 0) {
    running = false;
    activeAbort = null;
    try { stopBtn.style.display = "none"; } catch {}
  }
}

function doTarget(arg) {
  const [action, name] = parseTargetArg(arg || "");
  const [kind, newState, detail] = targetOutcome(targetElement, action, name);
  if (kind === "show_empty") {
    print(`  No target set. Usage: ${yellow("/target <element>")}`);
    return;
  }
  if (kind === "show") {
    print(`  Target: ${bold(yellow(esc(newState)))}`);
    return;
  }
  // Mutating kinds: assign session target, then host-format message.
  targetElement = newState;
  if (kind === "clear_empty") {
    print("  No target was set.");
  } else if (kind === "clear") {
    print(`  Target cleared (was ${yellow(esc(detail))}).`);
  } else {
    print(`  Target set: ${bold(yellow(esc(newState)))} — you'll be asked whether to continue the batch when this is crafted.`);
  }
  updateChrome();
}

/** Pause for y/n after target hit. Returns true if batch should stop. */
async function acknowledgeTargetHit(aName, bName, resultName) {
  // Serialize concurrent hits (API concurrency / multi-gen).
  const prev = targetHitChain;
  let release;
  targetHitChain = new Promise((r) => { release = r; });
  await prev;
  try {
    if (cancelled) return true;
    if (!(await confirmOrCancel(
      [`  ${bold(yellow("★ TARGET HIT ★"))} ${esc(aName)} + ${esc(bName)} = ${bold(yellow(esc(resultName)))}`],
      { reason: "target hit" },
    ))) {
      cancelled = true;
      cancelSleepers();
      print("  " + yellow("Stopped after target hit."));
      return true;
    }
    print("  " + dim("Continuing…"));
    return false;
  } finally {
    release();
  }
}

function pairTuples(pairs) {
  return pairs.map(([a, b]) => [
    a.text, a.emoji || "", !!a.discovered,
    b.text, b.emoji || "", !!b.discovered,
  ]);
}

function prioritizePairs(pairs) {
  // Kernel priority order — proven combiners first (ingredient-usage
  // score descending, pair-key tie-break); same ordering as the CLI.
  if (pairs.length < 2) return pairs;
  return pairsFromBoundary(prioritizePairsBoundary(pairTuples(pairs), recipeIndex));
}

// ── Bulk pair processor ──────────────────────────────────────────────
// opts (optional):
//   onResult({ a, b, result, isNew, isTarget, extra, done, total })
//     — after a successful combine (storage/history already updated)
//   shouldPrint(ctx) → boolean
//     — whether to emit the default `[n/total] result` line; default: print
//       only when the result is new or hits the target
//   skipSummary — omit the final "Done: N new…" line (crawl gens use their own)
async function runPairsInner(pairs, opts = {}) {
  pairs = prioritizePairs(pairs);
  let done = 0, newCount = 0, nothingCount = 0, errors = 0;
  const total = pairs.length;
  const onResult = opts.onResult;
  const shouldPrint = opts.shouldPrint;
  setLaneProgress("pair", 0, total);
  for (let i = 0; i < pairs.length; i++) {
    const [a, b] = pairs[i];
    if (cancelled) { print("  " + yellow("Cancelled.")); break; }
    // Bump progress when the pair *starts* so chrome never sits at 0/N
    // while a fetch (or rate-limit wait) is in flight.
    setLastPair(a.text, b.text);
    setLaneProgress("pair", i + 1, total);
    try {
      const result = await apiPair(a.text, b.text);
      if (cancelled) { print("  " + yellow("Cancelled.")); break; }
      done = i + 1;
      if (result) {
        const isNew = addElement(result.text, result.emoji, result.discovered);
        recordRecipe(result.text, a.text, b.text);
        history.push({ a: a.text, b: b.text, result: result.text });
        let extra = "";
        if (isNew) {
          newCount++;
          extra += " " + green("(new)");
        }
        const hit = isTargetHitKernel(targetElement, result.text || "");
        if (hit) {
          extra += " " + bold(yellow("★ TARGET ★"));
        }
        const ctx = { a, b, result, isNew, isTarget: hit, extra, done, total };
        const wantPrint = shouldPrint ? shouldPrint(ctx) : !!extra;
        if (wantPrint) {
          print(`  ${dim(`[${done}/${total}]`)} ${formatResult(a, b, result)}${extra}`);
        }
        if (onResult) await onResult(ctx);
        if (hit) {
          if (await acknowledgeTargetHit(a.text, b.text, result.text || "")) break;
        }
      } else {
        nothingCount++;
        history.push({ a: a.text, b: b.text, result: "Nothing" });
      }
    } catch (e) {
      if (cancelled) { print("  " + yellow("Cancelled.")); break; }
      done = i + 1;
      errors++;
    }
    await new Promise(r => setTimeout(r, 0));
  }
  if (!cancelled && !opts.skipSummary) {
    print(`  Done: ${green(String(newCount))} new, ${dim(String(nothingCount))} nothing, ${errors ? red(String(errors)) + " errors" : "0 errors"} (${done}/${total})`);
  }
}

async function confirmAndRunPairs(pairs) {
  try {
    beginRun();
    if (shouldBulkWarn(pairs.length, BULK_WARN)) {
      if (!(await confirmOrCancel([], { reason: `${pairs.length} pairs` }))) {
        print("  Cancelled.");
        return;
      }
    }
    if (cancelled) return;
    print(`  Running ${bold(String(pairs.length))} combinations...`);
    await runPairsInner(pairs);
  } finally {
    endRun();
  }
}

/** Print optional context, await y/n, return true to continue / false if cancelled.

    Reason belongs on the job chrome next to the prompt. Keybindings live only
    on confirm [y/n]>.
 */
async function confirmOrCancel(warnLines, { reason } = {}) {
  for (const line of warnLines || []) print(line);
  confirmReason = reason || "";
  try {
    const answer = await waitForConfirmKey();
    return !(cancelled || answer === "__cancelled__" || !confirmShouldContinue(answer));
  } finally {
    confirmReason = "";
    updateChrome();
  }
}

function waitForConfirmKey() {
  // Instant single-key y/n (no Enter) when the input is empty.
  // Esc / Stop cancel. Blank Enter ignored. Other Enter lines enqueue
  // (tryEnqueue) so API commands can queue during confirm; local commands
  // fall through to the main keydown handler.
  return new Promise((resolve) => {
    waitingForConfirm = true;
    // finish, not resolve: external settlers (Stop) must run cleanup() too,
    // or the capture keydown handler leaks and eats the next y/n/Esc press.
    confirmResolve = finish;
    if (promptEl) promptEl.textContent = "confirm [y/n]>";
    if (input) {
      input.value = "";
      input.placeholder = "";
      try { input.readOnly = false; } catch {}
    }
    updateChrome();
    function cleanup() {
      waitingForConfirm = false;
      confirmResolve = null;
      if (promptEl) promptEl.textContent = "craft>";
      if (input) {
        input.placeholder = "Type /help for commands";
        try { input.readOnly = false; } catch {}
      }
      try { input.removeEventListener("keydown", handler, true); } catch {}
      updateChrome();
    }
    function finish(val) {
      cleanup();
      resolve(val);
    }
    function handler(e) {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopImmediatePropagation();
        finish("__cancelled__");
        return;
      }
      // Instant y/n only when the field is empty (not mid-command).
      const empty = !input || !input.value;
      if (empty && isConfirmAnswer(e.key)) {
        e.preventDefault();
        e.stopImmediatePropagation();
        finish(confirmAnswerKey(e.key));
        return;
      }
      if (e.key === "Enter") {
        const val = input ? input.value.trim() : "";
        if (!val) {
          e.preventDefault();
          e.stopImmediatePropagation();
          return; // blank Enter ignored
        }
        if (isLocalCommand(val)) return; // main handler runs locals
        e.preventDefault();
        e.stopImmediatePropagation();
        if (input) input.value = "";
        if (isConfirmAnswer(val)) {
          finish(confirmAnswerKey(val));
        } else {
          tryEnqueue(val);
        }
        return;
      }
      // Other keys: allow typing a command while confirm is open.
    }
    try {
      input.addEventListener("keydown", handler, true);
      input.focus();
    } catch (err) {
      cleanup();
      throw err;
    }
  });
}

// ── Commands ─────────────────────────────────────────────────────────

function doSearch(query) {
  const { matches, error } = matchElements(query);
  if (error) { print("  " + red(error)); return; }
  if (!matches.length) { print("  No matches found."); return; }
  for (const el of matches) print("  " + formatElement(el));
}

function doList() {
  const elements = getAllElements();
  if (!elements.length) { print("  No elements discovered."); return; }
  print(`  ${green(String(elements.length))} elements:`);
  for (const el of elements) print("  " + formatElement(el));
}

function traceRecipeCore(name) {
  const [status, target, steps] = traceRecipeBoundary(elementTuples(), recipeIndex, name);
  return { status, target, steps };
}

function doRecipe(name) {
  const { status, target: targetName, steps } = traceRecipeCore(name);
  switch (status) {
    case 0:
      print("  " + red("Element not found."));
      return;
    case 1: {
      const el = resolveElement(targetName);
      print(`  ${formatElement(el)} is a base element.`);
      return;
    }
    case 2:
      print("  " + yellow("No recipe known. Try /import or /fill."));
      return;
    case 3:
      print("  " + yellow("Cannot trace full lineage — some intermediate recipes missing."));
      return;
    case 4: {
      const el = resolveElement(targetName);
      print(`  Recipe for ${formatElement(el)} (${bold(String(steps.length))} steps):`);
      for (let i = 0; i < steps.length; i++) {
        const [a, b, result] = steps[i];
        const aEl = resolveElement(a);
        const bEl = resolveElement(b);
        const rEl = resolveElement(result);
        print(`  ${dim(String(i + 1) + ".")} ${formatElement(aEl)} + ${formatElement(bEl)} = ${formatElement(rEl)}`);
      }
      return;
    }
  }
}

async function doExhaust(query) {
  const { matches, error } = matchElements(query);
  if (error) { print("  " + red(error)); return; }
  if (!matches.length) { print(`  No elements match: ${esc(query)}`); return; }

  const pairs = pairsFromBoundary(
    exhaustPairsBoundary(toTuples(matches), elementTuples())
  );
  if (!pairs.length) {
    print(`  No valid pairs for query: ${esc(query)}`);
    return;
  }
  print(`  Exhausting ${matches.length} element(s) matching ${yellow(esc(query))} with all discoveries (${pairs.length} pairs)...`);
  if (matches.length <= 10) {
    for (const m of matches) print(`    ${formatElement(m)}`);
  }
  await confirmAndRunPairs(pairs);
}

async function doCrawl(aName, bName) {
  const a = resolveElement(aName);
  const b = resolveElement(bName);
  print(`  Crawling from ${formatElement(a)} + ${formatElement(b)}...`);

  try {
    beginRun();
    // Uniform generations from a seeded pool (owner rulings 2026-08-07,
    // matching the CLI): the seed pair is just part of generation 1
    // (which also holds the self-pairs), pair order comes from the
    // kernel's sorted-name generation, and any result not already in the
    // pool joins the next generation's pool — not only globally-new
    // discoveries. Pair fetch/progress/cancel/target-ack is shared via
    // runPairsInner; pool bookkeeping + selective print stay here.
    const pool = new Map([[a.text, a], [b.text, b]]);
    const triedKeys = [];
    let gen = 1;
    while (!cancelled) {
      const [rawPairs, newKeys] = crawlGenerationPairsBoundary(
        toTuples([...pool.values()]),
        triedKeys
      );
      const pairs = pairsFromBoundary(rawPairs);
      for (const k of newKeys) triedKeys.push(k);
      if (!pairs.length) { print("  " + dim("No more untried pairs.")); break; }
      print(`  ${dim(`Gen ${gen}:`)} ${pairs.length} pairs to try...`);
      let newInGen = 0;
      await runPairsInner(pairs, {
        skipSummary: true,
        // Suppress default `[n/total]` lines; crawl prints new/target only
        // without the ordinal prefix.
        shouldPrint: () => false,
        onResult: ({ a: pa, b: pb, result: r, isNew, isTarget, extra }) => {
          addElement(pa.text, pa.emoji, false);
          addElement(pb.text, pb.emoji, false);
          if (!pool.has(r.text)) {
            pool.set(r.text, { text: r.text, emoji: r.emoji, discovered: r.discovered });
            newInGen++;
          }
          if (isNew || isTarget) {
            print(`  ${formatResult(pa, pb, r)}${extra}`);
          }
        },
      });
      if (cancelled) break;
      print(`  ${dim(`Gen ${gen} done:`)} ${green(String(newInGen))} new elements.`);
      if (newInGen === 0) break;
      gen++;
    }
    if (cancelled) print("  " + yellow("Crawl cancelled."));
    else print(`  Pool size: ${bold(String(pool.size))} elements.`);
  } finally {
    endRun();
  }
}

async function doPermute(query) {
  const { matches, error } = matchElements(query);
  if (error) { print("  " + red(error)); return; }
  if (!matches.length) { print("  No elements match that query."); return; }
  if (matches.length === 1) {
    print(`  Only one match: ${formatElement(matches[0])}. Need at least two.`);
    return;
  }
  const pairs = pairsFromBoundary(permutePairsBoundary(toTuples(matches)));
  print(`  ${matches.length} elements match, ${pairs.length} unique pairs:`);
  for (const m of matches) print(`    ${formatElement(m)}`);
  await confirmAndRunPairs(pairs);
}

async function doPermutate(query) {
  let round = 0;
  let confirmed = false;
  let stopped = false;
  print(`  Permutating matches for ${yellow(esc(query))} until no new discoveries...`);

  try {
    beginRun();
    while (true) {
      if (cancelled) { stopped = true; break; }
      if (round >= MAX_PERMUTATE_ROUNDS) {
        print(`  Reached max rounds (${MAX_PERMUTATE_ROUNDS}). Stopping.`);
        break;
      }
      round++;
      const knownBefore = new Set(getAllElements().map(e => e.text));
      const { matches, error } = matchElements(query);
      if (error) { print("  " + red(error)); return; }
      if (!matches.length) { print("  No elements match that query."); return; }
      if (matches.length === 1) {
        print(`  Only one match: ${formatElement(matches[0])}. Need at least two.`);
        return;
      }

      const pairs = pairsFromBoundary(permutePairsBoundary(toTuples(matches)));
      print(`  ${dim(`--- Round ${round}:`)} ${matches.length} elements, ${pairs.length} pairs ---`);

      if (!confirmed && shouldBulkWarn(pairs.length, BULK_WARN)) {
        if (!(await confirmOrCancel([], { reason: `${pairs.length} pairs per round` }))) {
          print("  Cancelled.");
          return;
        }
        confirmed = true;
      }

      await runPairsInner(pairs);
      if (cancelled) { stopped = true; break; }

      const knownAfter = new Set(getAllElements().map(e => e.text));
      let newCount = 0;
      for (const name of knownAfter) {
        if (!knownBefore.has(name)) newCount++;
      }
      print(`  +${newCount} new elements`);
      if (newCount === 0) {
        print("  No new discoveries. Stopping.");
        break;
      }
    }
    if (stopped) print("  " + yellow("Stopped."));
    else print(`  Permutate done after ${round} round(s).`);
  } finally {
    endRun();
  }
}

async function doCross(leftQ, rightQ) {
  const leftResult = matchElements(leftQ);
  if (leftResult.error) { print("  " + red(leftResult.error)); return; }
  const rightResult = matchElements(rightQ);
  if (rightResult.error) { print("  " + red(rightResult.error)); return; }
  const left = leftResult.matches;
  const right = rightResult.matches;
  if (!left.length) { print(`  No elements match: ${esc(leftQ)}`); return; }
  if (!right.length) { print(`  No elements match: ${esc(rightQ)}`); return; }
  const pairs = pairsFromBoundary(crossPairsBoundary(toTuples(left), toTuples(right)));
  if (!pairs.length) {
    print("  No valid pairs (all matches overlap).");
    return;
  }
  const leftPreview = left.slice(0, 10).map(e => e.text).join(", ");
  const rightPreview = right.slice(0, 10).map(e => e.text).join(", ");
  print(`  Left (${left.length}): ${esc(leftPreview)}${left.length > 10 ? "..." : ""}`);
  print(`  Right (${right.length}): ${esc(rightPreview)}${right.length > 10 ? "..." : ""}`);
  print(`  ${pairs.length} unique pairs`);
  await confirmAndRunPairs(pairs);
}

async function doCombineWithQuery(name, query) {
  const target = resolveElement(name);
  const { matches: others, error } = matchElements(query);
  if (error) { print("  " + red(error)); return; }
  if (!others.length) { print(`  No elements match: ${esc(query)}`); return; }
  const pairs = pairsFromBoundary(
    withPairsBoundary(toTuples([target])[0], toTuples(others))
  );
  if (!pairs.length) { print(`  No other elements match: ${esc(query)}`); return; }
  print(`  Combining ${bold(esc(target.text))} with ${pairs.length} elements matching ${yellow(esc(query))}...`);
  await confirmAndRunPairs(pairs);
}

// Fetch with retry + backoff for 429s
async function fetchRetry(url) {
  // Attempt count, retryable statuses, and backoff all come from the kernel
  // (shared with the CLI). Status 0 = transport failure.
  for (let attempt = 0; ; attempt++) {
    if (cancelled) throw new Error("Cancelled");
    const guard = attemptSignal(FETCH_TIMEOUT_MS);
    let resp = null;
    let err = null;
    try {
      resp = await fetch(url, { signal: guard.signal });
    } catch (e) {
      // User Stop propagates; a timeout/network failure retries with backoff.
      if (cancelled || (activeAbort && activeAbort.signal.aborted)) throw e;
      err = e;
    } finally {
      guard.done();
    }
    if (resp && resp.status !== 429) return resp;
    const status = resp ? resp.status : 0;
    if (!ibShouldRetry(status, attempt)) {
      if (resp) return resp;
      throw err;
    }
    await sleepCancellable(ibRetryBackoffMs(attempt));
  }
}

function processRecipeSteps(steps) {
  const tuples = steps.map((step) => [
    String(step.a?.id || step.a?.text || ""), step.a?.emoji || "",
    String(step.b?.id || step.b?.text || ""), step.b?.emoji || "",
    String(step.result?.id || step.result?.text || ""), step.result?.emoji || "",
  ]);
  const [elementBatch, recipeBatch] = lineageStepsToBatches(tuples);
  addElementsBatch(elementBatch);
  recordRecipesBatch(recipeBatch);
  return recipeBatch;
}

function pickFile(accept) {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    if (accept) input.accept = accept;
    input.onchange = () => resolve(input.files[0] || null);
    // Dismissing the dialog must settle too, or the ib lane worker waits on
    // this promise forever and every later ib command queues behind it.
    input.oncancel = () => resolve(null);
    input.click();
  });
}

async function doImportFile() {
  try {
    beginRun();
    print("  Select a .ic save file...");
    const file = await pickFile(".ic");
    if (!file || cancelled) { print("  " + yellow("Cancelled.")); return; }
    print(`  Reading ${bold(esc(file.name))}...`);
    const arrayBuf = await file.arrayBuffer();
    let json;
    try {
      // Try gzip decompression first
      const stream = new Blob([arrayBuf]).stream().pipeThrough(new DecompressionStream("gzip"));
      const text = await new Response(stream).text();
      json = JSON.parse(text);
    } catch {
      // Fall back to plain JSON
      const text = new TextDecoder().decode(arrayBuf);
      json = JSON.parse(text);
    }
    const items = json.items || [];
    if (!items.length) { print("  No items in save file."); return; }
    const itemTuples = items.map((item) => [
      item.id,
      String(item.text ?? ""),
      item.emoji || "",
      !!(item.discovery || item.discovered),
    ]);
    const recipeRefs = [];
    for (const item of items) {
      if (!item.recipes) continue;
      for (const pair of item.recipes) {
        if (pair.length === 2) recipeRefs.push([item.id, pair[0], pair[1]]);
      }
    }
    const [elementBatch, recipeBatch] = icSaveToBatches(itemTuples, recipeRefs);
    const importedCount = addElementsBatch(elementBatch);
    recordRecipesBatch(recipeBatch);
    const recipeCount = recipeBatch.length;
    rebuildRecipeIndex();
    if (cancelled) print("  " + yellow("Import cancelled."));
    else print(`  Loaded ${green(String(items.length))} elements (${importedCount} new) with ${recipeCount} recipes from ${bold(esc(file.name))}.`);
  } catch (e) {
    if (!cancelled) print("  " + red(`Error reading save file: ${esc(e.message)}`));
  } finally {
    endRun();
  }
}

async function doImport(name) {
  try {
    beginRun();
    print(`  Importing ${bold(esc(name))} from Infinibrowser...`);
    const itemResp = await fetchRetry(`https://infinibrowser.wiki/api/item?id=${encodeURIComponent(name)}`);
    if (cancelled) return;
    if (!itemResp.ok) { print("  " + red("Element not found on Infinibrowser.")); return; }
    const recipeResp = await fetchRetry(`https://infinibrowser.wiki/api/recipe?id=${encodeURIComponent(name)}`);
    if (cancelled) return;
    if (!recipeResp.ok) { print("  " + red("No recipe found on Infinibrowser.")); return; }
    const recipeData = await recipeResp.json();
    const steps = recipeData.steps || recipeData.recipe || [];
    if (!steps.length) { print("  " + yellow("No recipe steps found.")); return; }
    const recipeBatch = processRecipeSteps(steps);
    rebuildRecipeIndex();
    print(`  Imported ${green(String(recipeBatch.length))} recipe steps for ${bold(esc(name))}.`);
  } catch (e) {
    if (!cancelled) print("  " + red(`Import failed: ${esc(e.message)}. CORS may be blocked — try the Python CLI instead.`));
  } finally {
    endRun();
  }
}

async function doFill() {
  const elements = getAllElements();
  // Snapshot of still-unfilled names; updated as each lineage lands so
  // intermediates filled by a prior fetch are skipped (matches CLI).
  const stillMissing = new Set(unfilledNamesBoundary(elementTuples(), recipeIndex));
  const missing = elements.filter(e => stillMissing.has(e.text));
  if (!missing.length) { print("  All elements have recipes."); return; }
  print(`  ${yellow(String(missing.length))} elements missing recipes. Fetching from Infinibrowser...`);
  let filled = 0, errors = 0, skipped = 0;
  try {
    beginRun();
    setLaneProgress("ib", 0, missing.length);
    for (let i = 0; i < missing.length; i++) {
      if (cancelled) { print("  " + yellow("Fill cancelled.")); break; }
      const el = missing[i];
      setLaneProgress("ib", i + 1, missing.length);
      // Prior lineage may have already recorded a recipe for this name.
      if (!stillMissing.has(el.text)) {
        skipped++;
        continue;
      }
      try {
        const recipeResp = await fetchRetry(`https://infinibrowser.wiki/api/recipe?id=${encodeURIComponent(el.text)}`);
        if (recipeResp.ok) {
          const data = await recipeResp.json();
          const steps = data.steps || data.recipe || [];
          const recipeBatch = processRecipeSteps(steps);
          // Results in this lineage now have at least one recipe pair.
          for (const [resultName] of recipeBatch) stillMissing.delete(resultName);
          stillMissing.delete(el.text);
          if (recipeBatch.length) filled++;
          else errors++;
        } else {
          errors++;
        }
      } catch { errors++; }
      if ((i + 1) % 10 === 0 || i === missing.length - 1) {
        print(`  ${dim(`[${i + 1}/${missing.length}]`)} ${green(String(filled))} filled, ${skipped ? dim(String(skipped)) + " skipped, " : ""}${errors ? red(String(errors)) + " failed" : "0 failed"}`);
      }
      await sleepCancellable(500);
    }
  } finally {
    rebuildRecipeIndex();
    endRun();
  }
  print(`  Done: ${green(String(filled))} filled, ${skipped ? dim(String(skipped)) + " skipped (already filled by prior lineages), " : ""}${errors ? red(String(errors)) + " failed" : "0 failed"} (${missing.length} total).`);
}

function doUnfilled() {
  const elements = getAllElements();
  const missingNames = new Set(unfilledNamesBoundary(elementTuples(), recipeIndex));
  const missing = elements.filter(e => missingNames.has(e.text));
  if (!missing.length) { print("  All elements have recipes."); return; }
  print(`  ${yellow(String(missing.length))} elements without recipes:`);
  for (const el of missing) print("  " + formatElement(el));
}

function findOrphanCandidates() {
  return orphanCandidatesBoundary(elementTuples(), recipeIndex).map(
    ([text, emoji, discovered]) => ({ text, emoji, discovered })
  );
}

async function ibCanFill(name) {
  try {
    const itemResp = await fetchRetry(`https://infinibrowser.wiki/api/item?id=${encodeURIComponent(name)}`);
    if (itemResp.status === 404) return false;
    if (!itemResp.ok) return null;
    const itemData = await itemResp.json();
    if (itemData.code) return false;

    const recipeResp = await fetchRetry(`https://infinibrowser.wiki/api/recipe?id=${encodeURIComponent(name)}`);
    if (recipeResp.status === 404) return false;
    if (!recipeResp.ok) return null;
    const recipeData = await recipeResp.json();
    if (recipeData.code) return false;
    const steps = recipeData.steps || recipeData.recipe || [];
    return steps.length > 0;
  } catch {
    return null;
  }
}

async function doPrune() {
  const candidates = findOrphanCandidates();
  if (!candidates.length) { print("  Nothing to prune."); return; }
  print(`  ${yellow(String(candidates.length))} orphan element${candidates.length === 1 ? "" : "s"} to check on Infinibrowser...`);
  let pruned = 0, kept = 0, skipped = 0;
  try {
    beginRun();
    setLaneProgress("ib", 0, candidates.length);
    for (let i = 0; i < candidates.length; i++) {
      if (cancelled) { print("  " + yellow("Prune cancelled.")); break; }
      const el = candidates[i];
      setLaneProgress("ib", i + 1, candidates.length);
      const fillable = await ibCanFill(el.text);
      if (fillable === null) {
        skipped++;
      } else if (fillable) {
        kept++;
      } else {
        await removeElement(el.text);
        pruned++;
      }
      if ((i + 1) % 10 === 0 || i === candidates.length - 1) {
        print(`  ${dim(`[${i + 1}/${candidates.length}]`)} ${green(String(pruned))} pruned, ${kept} kept, ${skipped ? yellow(String(skipped)) + " skipped" : "0 skipped"}`);
      }
      await sleepCancellable(500);
    }
  } finally {
    rebuildIndexes();
    rebuildRecipeIndex();
    endRun();
  }
  print(`  Done: ${green(String(pruned))} pruned, ${kept} fillable on Infinibrowser (kept), ${skipped ? yellow(String(skipped)) + " skipped (API errors)" : "0 skipped"}.`);
}

async function doExport() {
  // Kernel export builder: fresh sequential ids over the closure, recipes
  // remapped — an export can never reference an excluded item.
  const [itemTuples, recipeRefs] = buildExportItemsBoundary(elementTuples(), recipeIndex);
  const exportItems = itemTuples.map(([id, text, emoji, first]) => {
    const exportItem = { id, text, emoji: emoji || "" };
    if (first) exportItem.discovery = true;
    return exportItem;
  });
  for (const [resultId, aId, bId] of recipeRefs) {
    (exportItems[resultId].recipes ||= []).push([aId, bId]);
  }
  const now = Date.now();
  const save = { name: "Trainer Export", version: "1.0", created: now, updated: now, instances: [], items: exportItems };
  const json = JSON.stringify(save);
  // Gzip compress to match the .ic format the game and Python CLI expect
  const stream = new Blob([json]).stream().pipeThrough(new CompressionStream("gzip"));
  const gzipped = await new Response(stream).blob();
  const url = URL.createObjectURL(gzipped);
  const a = document.createElement("a");
  a.href = url;
  a.download = "infinite-craft-export.ic";
  a.click();
  URL.revokeObjectURL(url);
  print(`  Exported ${green(String(exportItems.length))} elements (gzip compressed).`);
}

function doHistory() {
  if (!history.length) { print("  No combinations this session."); return; }
  print(`  ${bold(String(history.length))} combinations:`);
  for (const h of history) {
    print(`  ${esc(h.a)} + ${esc(h.b)} = ${esc(h.result)}`);
  }
}

function doHelp() {
  print(`  ${bold("Combine:")}
    ${cyan("<element> + <element>")}       Combine two elements
    ${cyan("/combine <element> <element>")}  Combine two elements

  ${bold("Crawl:")}
    ${cyan("<element> ++ <element>")}      Combine & crawl until no new discoveries
    ${cyan("/crawl <element> <element>")}  Combine & crawl until no new discoveries

  ${bold("Bulk combine (query syntax below):")}
    ${cyan("<element> +| <query>")}        Combine element with all matching discoveries
    ${cyan("/with <element> <query>")}     Combine element with all matching discoveries
    ${cyan("<query> * <query>")}           Cross-combine matches from both queries
    ${cyan("/cross <query> <query>")}    Cross-combine matches from both queries
    ${cyan("/permute <query>")}            Combine all matching elements with each other
    ${cyan("/permutate <query>")}          Permute repeatedly until no new discoveries
    ${cyan("/exhaust <query>")}            Each match combined with all discoveries

  ${bold("Query syntax (/search, /with, /permute, /permutate, /cross, /exhaust, shorthands):")}
    substring                   Default: case-insensitive substring
    * ? []                      fnmatch wildcards (e.g. fire*, mu?)
    /pattern/                   Regex, case-insensitive (| alternation, \\d escapes)
    !<query>                    Exclude matches (e.g. !fire* = everything except fire*)
    !                           All elements (exclude nothing)
    ^<query>                    First discoveries only (e.g. ^fire* = new fire* matches)
    ^                           All first discoveries

  ${bold("Discoveries & recipes:")}
    ${cyan("/search <query>")}             Search discoveries
    ${cyan("/recipe <element>")}           Show shortest recipe from base elements
    ${cyan("/list")}                       List all discovered elements
    ${cyan("/import <element|file.ic>")}   Import from Infinibrowser or .ic save file
    ${cyan("/fill")}                       Fetch missing recipes from Infinibrowser
    ${cyan("/unfilled")}                   List elements without recipes
    ${cyan("/prune")}                      Remove orphan elements Infinibrowser can't fill
    ${cyan("/export")}                     Download discoveries as .ic save file
    ${cyan("/history")}                    Show combinations this session
    ${cyan("/target [element|clear]")}     Watch for a result; ask y/n to continue on hit
    ${cyan("/clear")}                      Clear output (browser only)
    ${cyan("/help")}                       Show this help`);
}

function setLaneProgress(lane, done, total) {
  if (lane === "ib") {
    ibJobDone = done;
    ibJobTotal = total;
  } else {
    jobDone = done;
    jobTotal = total;
  }
  updateChrome();
}

function setLastPair(a, b) {
  lastPairA = a;
  lastPairB = b;
  updateChrome();
}

function clearLaneProgress(lane) {
  if (lane === "ib") {
    ibJobDone = 0;
    ibJobTotal = 0;
  } else {
    jobDone = 0;
    jobTotal = 0;
    lastPairA = null;
    lastPairB = null;
  }
  updateChrome();
}

function rateBarHtml(remaining, max, oldestFracMilli) {
  const [leftFilled, rightFilled] = rateBarFills(
    remaining, max, oldestFracMilli, RATE_BAR_LEFT, RATE_BAR_RIGHT
  );
  const left = rateBarSegment(leftFilled, RATE_BAR_LEFT);
  const right = rateBarSegment(rightFilled, RATE_BAR_RIGHT);
  // No separator: purple next-slot wait (left 1/2) + cyan capacity (right 1/2).
  return (
    `<span class="ict-rate-bar ict-rate-bar-age">${left}</span>` +
    `<span class="ict-rate-bar ict-rate-bar-cap">${right}</span>` +
    ` <span class="ict-rate-num">${remaining}/${max}</span>`
  );
}

function updateChrome() {
  if (!rateEl || !jobEl || !queueEl) return;

  // Permanent rate line: segmented bar + optional last pair (pair lane only).
  const { remaining, max, oldestFracMilli } = rateChromeSnapshot();
  const ratePrefix = `<span class="ict-rate-label">rate</span> ${rateBarHtml(remaining, max, oldestFracMilli)}`;
  if (currentPairCommand && lastPairA != null && lastPairB != null) {
    // Width-aware: measure leftover cells after painting the rate segment once.
    rateEl.innerHTML = ratePrefix + ` <span class="ict-rate-sep">·</span> <span class="ict-rate-pair"></span>`;
    const pairSpan = rateEl.querySelector(".ict-rate-pair");
    const totalPx = rateEl.clientWidth || 320;
    const ageW = rateEl.querySelector(".ict-rate-bar-age")?.offsetWidth || 0;
    const capW = rateEl.querySelector(".ict-rate-bar-cap")?.offsetWidth || 0;
    const usedPx = (rateEl.querySelector(".ict-rate-label")?.offsetWidth || 0)
      + ageW + capW
      + (rateEl.querySelector(".ict-rate-num")?.offsetWidth || 0)
      + (rateEl.querySelector(".ict-rate-sep")?.offsetWidth || 0)
      + 16;
    const charPx = 7.2; // monospace ~13px font
    const avail = Math.max(8, Math.floor((totalPx - usedPx) / charPx));
    if (pairSpan) pairSpan.textContent = rateFormatPairForWidth(lastPairA, lastPairB, avail);
  } else {
    rateEl.innerHTML = ratePrefix;
  }
  rateEl.style.display = "block";

  // Job line(s): pair and/or IB can run concurrently (interlaced status).
  const jobParts = [];
  if (waitingForConfirm) {
    const extra = confirmReason
      ? ` <span class="ict-job-sep">·</span> <span class="ict-job-reason">${esc(confirmReason)}</span>`
      : "";
    jobParts.push(`<div class="ict-job-row"><span class="ict-job-mark">◆</span> <span class="ict-job-label">confirm</span> <span class="ict-job-cmd">${esc(currentPairCommand)}</span>${extra}</div>`);
  } else if (currentPairCommand) {
    const prog = jobTotal > 0 ? ` <span class="ict-job-prog">${jobDone}/${jobTotal}</span>` : "";
    jobParts.push(`<div class="ict-job-row"><span class="ict-job-mark">▶</span> <span class="ict-job-label">running</span> <span class="ict-job-cmd">${esc(currentPairCommand)}</span>${prog}</div>`);
  }
  if (currentIbCommand) {
    const prog = ibJobTotal > 0 ? ` <span class="ict-job-prog">${ibJobDone}/${ibJobTotal}</span>` : "";
    jobParts.push(`<div class="ict-job-row"><span class="ict-job-mark">▶</span> <span class="ict-job-label">running</span> <span class="ict-job-cmd">${esc(currentIbCommand)}</span>${prog}</div>`);
  }
  if (jobParts.length) {
    jobEl.style.display = "block";
    jobEl.innerHTML = jobParts.join("");
  } else {
    jobEl.style.display = "none";
    jobEl.innerHTML = "";
  }

  // Queue: pending from both lanes, interlaced (pair first then ib is fine —
  // they drain independently).
  const pending = [...pairQueue, ...ibQueue];
  if (pending.length) {
    queueEl.style.display = "block";
    let html = `<div class="ict-queue-label">Queue:</div>`;
    for (const cmd of pending) {
      html += `<div class="ict-queue-item">${esc(cmd)}</div>`;
    }
    queueEl.innerHTML = html;
  } else {
    queueEl.style.display = "none";
    queueEl.innerHTML = "";
  }
}

function totalPending() {
  return pairQueue.length + ibQueue.length;
}

function enqueueCommand(line) {
  const lane = commandQueueLane(line);
  const ib = lane === "ib";
  const current = ib ? currentIbCommand : currentPairCommand;
  const queue = ib ? ibQueue : pairQueue;
  // Fold workerRunning into confirm_active; pair also maps waitingForConfirm.
  // Kernel rule: current non-empty OR pending_len>0 OR confirm_active.
  const confirmActive = ib
    ? ibWorkerRunning
    : (waitingForConfirm || pairWorkerRunning);
  const laneBusy = queueLaneBusy(current, queue.length, confirmActive);
  if (ib) ibQueue.push(line);
  else pairQueue.push(line);
  updateChrome();
  if (laneBusy) print("  " + dim(`Queued: ${esc(line)}`));
  if (ib) ensureLaneWorker(ibCfg);
  else ensureLaneWorker(pairCfg);
}

function tryEnqueue(line) {
  const errorSegments = validateCommandLineSegments(line);
  if (errorSegments) {
    print(renderErrorSegments(errorSegments));
    return false;
  }
  const lane = commandQueueLane(line);
  const ib = lane === "ib";
  const current = ib ? currentIbCommand : currentPairCommand;
  const laneQueue = ib ? ibQueue.slice() : pairQueue.slice();
  const decision = queueAccept(
    line,
    current,
    laneQueue,
    totalPending(),
    MAX_QUEUE_DEPTH,
  );
  if (decision === "dup") {
    print("  " + dim("Already queued."));
    return false;
  }
  if (decision === "full") {
    print("  " + yellow(`Queue full (max ${MAX_QUEUE_DEPTH}).`));
    return false;
  }
  enqueueCommand(line);
  return true;
}

// Pair: confirm UI cleanup on error. Cancel-reset is peer-aware via kernel
// (lane_should_reset_cancel): both lanes share cancelled; reset only when peer idle.
const pairCfg = {
  lane: "pair",
  queue: pairQueue,
  getCurrent: () => currentPairCommand,
  setCurrent: (v) => { currentPairCommand = v; },
  getRunning: () => pairWorkerRunning,
  setRunning: (v) => { pairWorkerRunning = v; },
  clearJob: () => clearLaneProgress("pair"),
  cleanupConfirmOnError: true,
};
const ibCfg = {
  lane: "ib",
  queue: ibQueue,
  getCurrent: () => currentIbCommand,
  setCurrent: (v) => { currentIbCommand = v; },
  getRunning: () => ibWorkerRunning,
  setRunning: (v) => { ibWorkerRunning = v; },
  clearJob: () => clearLaneProgress("ib"),
  cleanupConfirmOnError: false,
};

async function ensureLaneWorker(cfg) {
  if (cfg.getRunning()) return;
  cfg.setRunning(true);
  try {
    while (cfg.queue.length) {
      // Peer = other lane's current command; confirm_active = waitingForConfirm.
      const peerBusy = cfg.lane === "ib"
        ? !!(currentPairCommand)
        : !!(currentIbCommand);
      if (laneShouldResetCancel(cfg.lane, peerBusy, waitingForConfirm)) {
        cancelled = false;
      }
      const line = cfg.queue.shift();
      cfg.setCurrent(line);
      cfg.clearJob();
      try {
        await executeCommand(line);
      } catch (err) {
        endRun();
        if (cfg.cleanupConfirmOnError) {
          waitingForConfirm = false;
          confirmResolve = null;
          if (promptEl) promptEl.textContent = "craft>";
          if (input) {
            input.placeholder = "Type /help for commands";
            try { input.readOnly = false; } catch {}
          }
        }
        print("  " + red("Error: " + esc(err && err.message || String(err))));
      }
      cfg.setCurrent("");
      cfg.clearJob();
    }
  } finally {
    cfg.setRunning(false);
    updateChrome();
  }
}

// ── Command dispatcher ───────────────────────────────────────────────
async function executeClassified(kind, payload, line) {
  // Bare /import opens the file picker — deliberately more permissive than
  // the kernel validator, so it dispatches before the validation gate.
  if (kind === "import") {
    if (!payload.trim()) await doImportFile();
    else if (payload.endsWith(".ic") || payload.includes("/") || payload.includes("\\")) await doImportFile();
    else await doImport(payload.trim());
    return;
  }
  // Validation-first: every usage/pipe/operator error is the kernel's
  // (rendered as escaped segments); the branches below run on valid lines.
  const errorSegments = validateCommandLineSegments(line);
  if (errorSegments) {
    print(renderErrorSegments(errorSegments));
    return;
  }
  if (kind === "permute") { await doPermute(payload.trim()); return; }
  if (kind === "permutate") { await doPermutate(payload.trim()); return; }
  if (kind === "fill") { await doFill(); return; }
  if (kind === "prune") { await doPrune(); return; }
  if (kind === "export") { await doExport(); return; }
  if (kind === "exhaust") { await doExhaust(payload.trim()); return; }
  if (kind === "combine" || kind === "crawl") {
    const parsed = parseTwoElements(payload);
    if (kind === "combine") await doCombine(parsed[0], parsed[1]);
    else await doCrawl(parsed[0], parsed[1]);
    return;
  }
  if (kind === "with") {
    const parsed = parseWithArgs(payload);
    await doCombineWithQuery(parsed[0], parsed[1]);
    return;
  }
  if (kind === "cross") {
    const parsed = parseCrossQueries(payload);
    await doCross(parsed[0], parsed[1]);
    return;
  }
  if (kind === "++") {
    const parsed = parseOperands(kind, payload);
    await doCrawl(parsed[0], parsed[1]);
    return;
  }
  if (kind === "+|") {
    const parsed = parseOperands(kind, payload);
    await doCombineWithQuery(parsed[0], parsed[1]);
    return;
  }
  if (kind === "*") {
    const parsed = parseOperands(kind, payload);
    await doCross(parsed[0], parsed[1]);
    return;
  }
  if (kind === "+") {
    const parsed = parseOperands(kind, payload);
    await doCombine(parsed[0], parsed[1]);
  }
}

async function executeCommand(line) {
  let rest;
  if ((rest = slashArgs(line, "/help")) !== null) { doHelp(); return; }
  if ((rest = slashArgs(line, "/search")) !== null) {
    if (!rest) print("  Usage: /search <query>");
    else doSearch(rest);
    return;
  }
  if ((rest = slashArgs(line, "/recipe")) !== null) {
    if (!rest) print("  Usage: /recipe <element>");
    else doRecipe(rest);
    return;
  }
  if ((rest = slashArgs(line, "/list")) !== null) { doList(); return; }
  if ((rest = slashArgs(line, "/history")) !== null) { doHistory(); return; }
  if ((rest = slashArgs(line, "/target")) !== null) { doTarget(rest); return; }
  if ((rest = slashArgs(line, "/clear")) !== null) { output.innerHTML = ""; return; }
  if ((rest = slashArgs(line, "/unfilled")) !== null) { doUnfilled(); return; }

  const classified = classifyCommandLine(line);
  if (!classified) {
    const errorSegments = validateCommandLineSegments(line);
    if (errorSegments) print(renderErrorSegments(errorSegments));
    return;
  }
  await executeClassified(classified[0], classified[1], line);
}

async function dispatch(line) {
  if (isLocalCommand(line)) {
    await executeCommand(line);
    return;
  }
  tryEnqueue(line);
}

export {
  matchElements,
  resolveElement,
  recordRecipe,
  traceRecipeCore,
};

// ── Browser bootstrap ────────────────────────────────────────────────
// Everything above this line is safe to import in Node (no eager DOM/
// IndexedDB access at module-evaluation time). Only actually mount the
// UI and open IndexedDB when running in a real browser.
function initBrowserUI() {
  // ── Singleton guard ──────────────────────────────────────────────────
  if (window.__ICTrainer) return;

  // ── CSS ──────────────────────────────────────────────────────────────
  const style = document.createElement("style");
  style.textContent = `
    #ict-container{position:fixed;bottom:0;left:0;right:0;z-index:999999;font-family:'Menlo','Consolas','Monaco',monospace;font-size:13px;line-height:1.4}
    #ict-header{background:#0f3460;color:#e0e0e0;padding:4px 10px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;user-select:none}
    #ict-header span{font-weight:bold}
    #ict-body{background:#1a1a2e;color:#e0e0e0;display:flex;flex-direction:column}
    #ict-output{overflow-y:auto;max-height:300px;padding:6px 10px;white-space:pre-wrap;word-break:break-word}
    #ict-output div{margin:1px 0}
    #ict-rate{display:block;border-top:1px solid #0f3460;padding:4px 10px;background:#0d1526;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    #ict-rate .ict-rate-label{color:#888;margin-right:6px}
    #ict-rate .ict-rate-bar{letter-spacing:0}
    #ict-rate .ict-rate-bar-age{color:#7c4dff}
    #ict-rate .ict-rate-bar-cap{color:#00bcd4}
    #ict-rate .ict-rate-num{color:#e0e0e0;margin-left:2px}
    #ict-rate .ict-rate-sep{color:#555;margin:0 4px}
    #ict-rate .ict-rate-pair{color:#e0e0e0}
    #ict-job{display:none;border-top:1px solid #0f3460;padding:4px 10px;background:#12182b;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    #ict-job .ict-job-mark{color:#ffeb3b;margin-right:4px}
    #ict-job .ict-job-label{color:#888;margin-right:6px}
    #ict-job .ict-job-cmd{color:#ffeb3b}
    #ict-job .ict-job-prog{color:#00bcd4;margin-left:8px}
    #ict-job .ict-job-hint{color:#888;margin-left:8px}
    #ict-job .ict-job-sep{color:#555;margin:0 4px}
    #ict-job .ict-job-reason{color:#888}
    #ict-queue{display:none;border-top:1px solid #0f3460;padding:4px 10px;background:#12182b;font-size:12px;max-height:80px;overflow-y:auto}
    #ict-queue .ict-queue-label{color:#ffeb3b;margin-bottom:2px}
    #ict-queue .ict-queue-item{margin:1px 0;opacity:.85}
    #ict-queue .ict-queue-tag{color:#888;margin-right:4px;font-size:11px}
    #ict-job .ict-job-row{margin:1px 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    #ict-input-row{display:flex;align-items:center;border-top:1px solid #0f3460;padding:4px 10px;background:#16213e}
    #ict-prompt{color:#00bcd4;margin-right:6px;white-space:nowrap}
    #ict-input{flex:1;background:transparent;border:none;outline:none;color:#e0e0e0;font:inherit;caret-color:#00bcd4}
    #ict-stop{display:none;background:#e53935;color:#fff;border:none;padding:2px 8px;margin-left:6px;cursor:pointer;font:inherit;border-radius:3px}
    .ict-green{color:#4caf50}.ict-magenta{color:#e040fb}.ict-dim{opacity:.5}.ict-bold{font-weight:bold}
    .ict-yellow{color:#ffeb3b}.ict-cyan{color:#00bcd4}.ict-red{color:#f44336}
  `;
  document.head.appendChild(style);

  // ── HTML ─────────────────────────────────────────────────────────────
  const container = document.createElement("div");
  container.id = "ict-container";
  container.innerHTML = `
    <div id="ict-header"><span>⚡ Infinite Craft Trainer</span><button id="ict-toggle" style="background:none;border:none;color:#e0e0e0;cursor:pointer;font-size:16px">▼</button></div>
    <div id="ict-body">
      <div id="ict-output"></div>
      <div id="ict-rate"></div>
      <div id="ict-job"></div>
      <div id="ict-queue"></div>
      <div id="ict-input-row">
        <span id="ict-prompt">craft&gt;</span>
        <input id="ict-input" autocomplete="off" spellcheck="false" placeholder="Type /help for commands">
        <button id="ict-stop">Stop</button>
      </div>
    </div>`;
  document.body.appendChild(container);
  window.__ICTrainer = true;
  document.dispatchEvent(new CustomEvent("ict-trainer-ready"));

  output = document.getElementById("ict-output");
  rateEl = document.getElementById("ict-rate");
  jobEl = document.getElementById("ict-job");
  queueEl = document.getElementById("ict-queue");
  input = document.getElementById("ict-input");
  promptEl = document.getElementById("ict-prompt");
  body = document.getElementById("ict-body");
  toggle = document.getElementById("ict-toggle");
  stopBtn = document.getElementById("ict-stop");

  let collapsed = false;
  toggle.addEventListener("click", (e) => {
    e.stopPropagation();
    collapsed = !collapsed;
    body.style.display = collapsed ? "none" : "flex";
    toggle.textContent = collapsed ? "▲" : "▼";
  });
  document.getElementById("ict-header").addEventListener("click", () => {
    toggle.click();
  });
  stopBtn.addEventListener("click", () => {
    cancelled = true;
    cancelSleepers();
    if (activeAbort) activeAbort.abort();
    if (waitingForConfirm && confirmResolve) {
      waitingForConfirm = false;
      const resolve = confirmResolve;
      confirmResolve = null;
      if (promptEl) promptEl.textContent = "craft>";
      if (input) {
        input.placeholder = "Type /help for commands";
        try { input.readOnly = false; } catch {}
      }
      resolve("__cancelled__");
      updateChrome();
    }
  });

  function handleTrainerWheel(e) {
    if (collapsed || body.style.display === "none") return;
    output.scrollTop += e.deltaY;
    e.preventDefault();
    e.stopPropagation();
  }
  container.addEventListener("wheel", handleTrainerWheel, { passive: false });

  input.focus();

  // ── Input handling ───────────────────────────────────────────────────
  input.addEventListener("keydown", (e) => {
    // During confirm the capture handler owns y/n/Esc/API enqueue; allow
    // local commands (e.g. /search, /list) through here on Enter.
    if (waitingForConfirm) {
      if (e.key !== "Enter" || !isLocalCommand(input.value.trim())) return;
    }
    if (e.key === "Enter") {
      const line = input.value.trim();
      if (!line) return;
      input.value = "";
      cmdHistory.push(line);
      cmdHistoryIdx = cmdHistory.length;
      print(cyan("craft&gt;") + " " + esc(line));
      dispatch(line).catch((err) => {
        endRun();
        waitingForConfirm = false;
        confirmResolve = null;
        clearLaneProgress("pair");
        print("  " + red("Error: " + esc(err && err.message || String(err))));
      });
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (cmdHistoryIdx > 0) { cmdHistoryIdx--; input.value = cmdHistory[cmdHistoryIdx]; }
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      if (cmdHistoryIdx < cmdHistory.length - 1) { cmdHistoryIdx++; input.value = cmdHistory[cmdHistoryIdx]; }
      else { cmdHistoryIdx = cmdHistory.length; input.value = ""; }
    }
  });

  // Rate bar ticker (refill while idle / drain visibility while busy).
  if (rateTickerId) clearInterval(rateTickerId);
  rateTickerId = setInterval(() => updateChrome(), RATE_TICK_MS);
  updateChrome();

  // ── Async init: load from IndexedDB then show welcome ────────────────
  print(dim("Loading game data..."));
  openDB().then(db => {
    _db = db;
    return Promise.all([loadAllItems(), loadSaves()]);
  }).then(([allItems, saves]) => {
    _allItems = allItems;
    _saveId = detectActiveSave(saves, allItems);
    _items = allItems.filter(i => i.saveId === _saveId);
    const saveName = (saves.find(s => s.id === _saveId) || {}).name || `Save ${_saveId}`;
    rebuildIndexes();
    rebuildRecipeIndex();
    startPageSync();
    output.innerHTML = "";
    print(bold(cyan("=== Infinite Craft Trainer ===")) + dim(`  v${TRAINER_VERSION}`));
    print(`  Active save: ${bold(esc(saveName))} (id=${_saveId})`);
    print(`  ${green(String(_items.length))} elements loaded.`);
    const withRecipes = _items.filter(i => i.recipes && i.recipes.length).length;
    print(`  ${green(String(withRecipes))} recipes known.`);
    print(`  Type ${yellow("/help")} for commands.`);
    print("");
  }).catch(err => {
    print(red("Failed to load game data: " + esc(err.message)));
  });
}

const isBrowser = typeof window !== "undefined" && typeof document !== "undefined";
if (isBrowser) {
  initBrowserUI();
}
