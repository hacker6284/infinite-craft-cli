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
  lucky_pairs_boundary as luckyPairsBoundary,
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
  auto_approve_outcome as autoApproveOutcome,
  relay_toggle_outcome as relayToggleOutcome,
  relay_reseed_entries as relayReseedEntries,
  rate_bar_split_segments as rateBarSplitSegments,
  effective_rate_limit as effectiveRateLimit,
  cooldown_duration_ms as cooldownDurationMs,
  bounty_poll_interval_ms as bountyPollIntervalMs,
  hive_wait_tick_ms as hiveWaitTickMs,
  hive_resweep_interval_ms as hiveResweepIntervalMs,
  bulk_confirm_required as bulkConfirmRequired,
  rate_status_note as rateStatusNote,
  script_parse as scriptParse,
  script_eval_expr_boundary as scriptEvalExprBoundary,
  script_eval_cond_boundary as scriptEvalCondBoundary,
  script_eval_num_boundary as scriptEvalNumBoundary,
  script_take_tuples as scriptTakeTuples,
  script_sample_tuples as scriptSampleTuples,
  script_set_op_boundary as scriptSetOpBoundary,
  script_union_tuples as scriptUnionTuples,
  is_known_slash_command as isKnownSlashCommand,
  runs_local as runsLocal,
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
  parse_with_args as parseWithArgs,
  parse_cross_queries as parseCrossQueries,
} from "./_sudo/craft.mjs";
import { TRAINER_VERSION } from "./version.mjs";

// ── Constants ────────────────────────────────────────────────────────
const RATE_LIMIT = 60;
const RATE_WINDOW = 60000;
const BULK_WARN = 200;
const MAX_QUEUE_DEPTH = 50;

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
let autoApprove = false; // /auto: skip bulk-size y/n confirms this session
// Hive-mind relay (shared pair-result cache) session state — consulted only
// when the user toggle is on AND the last ping succeeded; fails open.
let relayUserOn = true; // /relay session toggle
let relayReachable = null; // null = not yet pinged (warming)
let relayHits = 0;
let lastRenderedHits = 0; // for the one-shot bee pulse (fires when relayHits grows)
let relayContributed = 0;
let relaySeeded = false;
// Presence identity + same-IP arbitration (relay-fed)
const relaySessionId =
  (typeof crypto !== "undefined" && crypto.randomUUID && crypto.randomUUID().slice(0, 16)) ||
  String(Math.random()).slice(2, 18);
let rateLimitEffective = 0; // 0 = full budget; >0 = split share from the hive
// 429 cooldown: neal's 429 is an hours-long IP ban — stand down completely.
let cooldownUntil = 0; // epoch ms
let cooldownStrikes = 0;
// Bounty worker (serve the hive while idle)
let bountiesWorked = 0;
let bountyProgress = null; // [done, batch] while serving
const fleetTimestamps = []; // rate slots lent to bounty work (gold in bar)

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
  while (fleetTimestamps.length && fleetTimestamps[0] <= now - RATE_WINDOW) {
    fleetTimestamps.shift();
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
  const max = rateMax();
  const remaining = rateSlotsLeft(timestamps.length, max);
  return {
    remaining,
    max,
    oldestFracMilli: nextSlotFracMilli(now),
    fleetUsed: fleetTimestamps.length,
  };
}

function acquireRate(fleet = false) {
  return new Promise((resolve, reject) => {
    function tryAcquire() {
      if (cancelled) { reject(new Error("Cancelled")); return; }
      const now = Date.now();
      pruneRateTimestamps(now);
      if (timestamps.length < rateMax()) {
        timestamps.push(now);
        if (fleet) fleetTimestamps.push(now);
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

// ── Hive-mind relay (shared pair-result cache tier) ──────────────────
// Cache order everywhere: local pairCache → relay → neal.fun. A rate-limit
// slot is committed only after both cache tiers miss; fresh neal results
// are contributed back in the background. Every call fails open.
const RELAY_URL =
  (typeof window !== "undefined" && window.IC_RELAY_URL) ||
  "https://infinite-craft-relay.onrender.com";

function relayActive() {
  return relayUserOn && relayReachable === true;
}

function cooling() {
  return Date.now() < cooldownUntil;
}

function tripCooldown() {
  // Concurrent 429s from a single ban must not inflate the strike count.
  if (cooling()) return;
  cooldownStrikes++;
  cooldownUntil = Date.now() + Number(cooldownDurationMs(cooldownStrikes));
  const resume = new Date(cooldownUntil).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  print(red(`429 from neal.fun — standing down until ~${resume}`) + dim(" (the ban is IP-wide and lasts hours; hive lookups still work)"));
  updateChrome();
}

function relayPresenceState() {
  if (cooling()) return "cooled";
  if (currentPairCommand) return "running";
  if (bountyProgress !== null) return "serving";
  return "idle";
}

function relayHeaders() {
  const h = {
    "Content-Type": "application/json",
    "x-ic-session": relaySessionId,
    "x-ic-state": relayPresenceState(),
  };
  if (cooling()) h["x-ic-cooled-until"] = String(cooldownUntil);
  return h;
}

/** Fold the hive envelope into local state: split the per-IP budget by
    spending peers; adopt a sibling session's 429 cooldown. */
function relayApplyHive(data) {
  const hive = data && data.hive;
  if (!hive) return;
  const peers = Number(hive.peers) || 0;
  rateLimitEffective = peers > 1 ? Number(effectiveRateLimit(RATE_LIMIT, peers)) : 0;
  let cu = Number(hive.cooledUntil) || 0;
  const now = Date.now();
  if (cu > now) {
    // Clamp a relay-reported cooldown to the kernel max (fail-open): a
    // garbage or clock-skewed relay must never park us offline forever.
    cu = Math.min(cu, now + Number(cooldownDurationMs(3)));
    if (cu > cooldownUntil) {
      cooldownUntil = cu;
      updateChrome();
    }
  }
}

function rateMax() {
  return rateLimitEffective > 0 ? Math.min(rateLimitEffective, RATE_LIMIT) : RATE_LIMIT;
}

async function relayFetch(path, payload, timeoutMs) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const opts =
      payload == null
        ? { signal: ctl.signal, headers: relayHeaders() }
        : {
            method: "POST",
            headers: relayHeaders(),
            body: JSON.stringify(payload),
            signal: ctl.signal,
          };
    const resp = await fetch(RELAY_URL + path, opts);
    if (!resp.ok) return null;
    const data = await resp.json();
    relayApplyHive(data);
    return data;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

/** Batch lookup. Returns {key: {r, e}} (hits only) or null (unreachable). */
function relayMarkUnreachable() {
  relayReachable = false;
  // Dropping the tier drops arbitration — restore the full per-IP budget.
  rateLimitEffective = 0;
}

async function relayLookup(pairs) {
  const data = await relayFetch("/api/lookup", { pairs }, 4000);
  if (data == null) {
    relayMarkUnreachable();
    return null;
  }
  return data.results || {};
}

/** Relay {r, e} value → trainer pairCache value (null = Nothing). */
function relayResultToCache(v) {
  return v.r === null ? null : { text: v.r, emoji: v.e || "", discovered: false };
}

/** Fire-and-forget: share fresh neal results with the hive. */
function relayContribute(entries) {
  relayFetch("/api/contribute", { entries }, 8000).then((d) => {
    if (d && d.added) relayContributed += d.added;
  });
}

// ── bounty worker: serve the hive while idle ─────────────────────────
function bountyPreempted() {
  return !!(
    currentPairCommand ||
    currentIbCommand ||
    pairQueue.length ||
    ibQueue.length ||
    cooling() ||
    !relayActive()
  );
}

let bountyTimer = null;

function fleetSlotAvailable() {
  return rateChromeSnapshot().remaining > 0;
}

// One poll-and-serve pass. Returns a status the scheduler paces on:
// "worked" (served fully, rate to spare → poll again now), "empty",
// "blocked" (preempted or out of rate), "unreachable". Serving never blocks
// on a rate slot — it checks availability first and backs off to polling.
async function bountyTick() {
  if (bountyPreempted()) return "blocked";
  const left = rateChromeSnapshot().remaining;
  if (left <= 0) return "blocked";
  // Claim only as many as we can serve this window (review finding F3).
  const data = await relayFetch(`/api/bounties?limit=${Math.min(5, left)}`, null, 4000);
  if (data == null) { relayMarkUnreachable(); return "unreachable"; }
  if (!Array.isArray(data.bounties) || !data.bounties.length) return "empty";
  const items = data.bounties;
  let done = 0;
  let status = "worked";
  bountyProgress = [0, items.length];
  updateChrome();
  try {
    for (const it of items) {
      if (bountyPreempted() || !fleetSlotAvailable()) { status = "blocked"; break; }
      const aName = it.first, bName = it.second;
      if (!aName || !bName) continue;
      const key = pairKey(aName, bName);
      // Always a fresh neal call (fleet=true skips local + hive caches):
      // every contribution is then an independent sighting for peer review,
      // and a poisoned local entry can never be re-propagated to the hive.
      let res;
      try {
        res = await apiPair(aName, bName, true);
      } catch (e) {
        const msg = String(e && e.message);
        if (msg.includes("429") || msg.includes("Cancelled")) { status = "blocked"; break; }
        continue;
      }
      pairCache.set(key, res);
      // Record the recipe locally too (host parity: the Python bounty worker
      // does this, so both hosts' recipe indexes stay in sync).
      if (res) recordRecipe(res.text, aName, bName);
      const [ka, kb] = pairKeyKernel(aName, bName);
      const added = await relayFetch(
        "/api/contribute",
        { entries: [[ka, kb, res ? res.text : null, res ? res.emoji : ""]] },
        8000
      );
      if (added == null) { relayMarkUnreachable(); status = "unreachable"; break; }
      relayContributed += added.added || 0;
      done++;
      bountiesWorked++;
      bountyProgress = [done, items.length];
      updateChrome();
    }
  } finally {
    bountyProgress = null;
    updateChrome();
  }
  // Served nothing (e.g. every pair errored) → don't report "worked", or the
  // scheduler skips its backoff and bursts the budget (review finding F1).
  if (status === "worked" && done === 0) status = "blocked";
  return status;
}

// Self-scheduling (setTimeout, not setInterval) so cycles never overlap:
// after a fully-served batch, poll again immediately to drain the board while
// rate lasts; otherwise wait a poll interval (which doubles as the presence
// heartbeat).
function scheduleBountyTick() {
  bountyTick()
    .then((status) => {
      const delay = status === "worked" ? 0 : Number(bountyPollIntervalMs());
      bountyTimer = setTimeout(scheduleBountyTick, delay);
    })
    .catch(() => {
      bountyTimer = setTimeout(scheduleBountyTick, Number(bountyPollIntervalMs()));
    });
}

function startBountyWorker() {
  if (bountyTimer) clearTimeout(bountyTimer);
  scheduleBountyTick();
}

/** Ping (wakes a spun-down free instance), then re-seed the hive once per
    session from this save's recipe index. */
async function relayWarmup() {
  if (!relayUserOn) return;
  let health = await relayFetch("/health", null, 8000);
  if (health == null) {
    // Free instances cold-start in tens of seconds; one more try.
    await new Promise((r) => setTimeout(r, 20000));
    health = await relayFetch("/health", null, 8000);
  }
  relayReachable = health != null && !!health.ok;
  if (!relayReachable || relaySeeded || !relayUserOn) return;
  const entries = relayReseedEntries(elementTuples(), recipeIndex).map((t) =>
    Array.from(t)
  );
  for (let i = 0; i < entries.length; i += 2000) {
    const d = await relayFetch(
      "/api/contribute",
      { entries: entries.slice(i, i + 2000) },
      8000
    );
    if (d == null) {
      relayMarkUnreachable();
      return;
    }
    relayContributed += d.added || 0;
  }
  relaySeeded = true;
}

async function apiPair(firstName, secondName, fleet = false) {
  if (cancelled) throw new Error("Cancelled");
  const key = pairKey(firstName, secondName);
  // Review bounties must re-ask neal — a cached answer is exactly what a
  // review is meant to independently verify.
  if (!fleet && pairCache.has(key)) return pairCache.get(key);
  if (!fleet && relayActive()) {
    const found = await relayLookup([[firstName, secondName]]);
    if (found && found[key] !== undefined) {
      const result = relayResultToCache(found[key]);
      pairCache.set(key, result);
      relayHits++;
      return result;
    }
  }
  if (cooling()) throw new Error("429 cooldown");
  await acquireRate(fleet);
  if (cancelled) throw new Error("Cancelled");
  const url = `/api/infinite-craft/pair?first=${encodeURIComponent(firstName)}&second=${encodeURIComponent(secondName)}`;
  let resp;
  let json = null;
  for (let attempt = 0; ; attempt++) {
    if (cancelled) throw new Error("Cancelled");
    const guard = attemptSignal(FETCH_TIMEOUT_MS);
    try {
      resp = await fetch(url, { signal: guard.signal });
      // 429 is an hours-long IP ban, never a retry candidate: stand down.
      if (resp.status === 429) { tripCooldown(); throw new Error("429 cooldown"); }
      // Body read shares the attempt guard so a stalled body times out too.
      if (resp.ok) { json = await resp.json(); break; }
    } catch (e) {
      if (String(e && e.message).includes("429 cooldown")) throw e;
      /* retry */
    } finally {
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
  // Contribute back — but NOT for fleet (bounty) serving: the bounty path
  // contributes explicitly and awaits the count, so contributing here too
  // would double-post the same entry (review finding T2; matches Python,
  // where client.pair never contributes and the bounty path does).
  if (!fleet && relayActive()) {
    const [ka, kb] = pairKeyKernel(firstName, secondName);
    relayContribute([[ka, kb, result ? result.text : null, result ? result.emoji : ""]]);
  }
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

// ── Background-freeze guard ──────────────────────────────────────────
// Chrome freezes hidden tabs after ~5 minutes (Memory Saver / Energy
// Saver), pausing timers, fetch completions — everything — so a bulk run
// in a background tab simply stops. Its freezing heuristics exempt pages
// holding Web Locks, so we hold one for exactly as long as a run is
// active and no longer: we only defeat the battery-saver while there is
// real work to protect. Best-effort — if the heuristic changes, runs
// pause in background again but nothing breaks.
let freezeGuardWanted = false;
let freezeGuardRelease = null;

function acquireFreezeGuard() {
  if (freezeGuardWanted) return;
  freezeGuardWanted = true;
  if (typeof navigator === "undefined" || !navigator.locks) return;
  try {
    navigator.locks.request("ict-active-run", { mode: "shared" }, () => {
      // Run ended before the grant arrived: return nothing so the lock
      // releases immediately instead of being held forever.
      if (!freezeGuardWanted) return undefined;
      return new Promise((resolve) => { freezeGuardRelease = resolve; });
    }).catch(() => {});
  } catch {}
}

function releaseFreezeGuard() {
  freezeGuardWanted = false;
  if (freezeGuardRelease) {
    freezeGuardRelease();
    freezeGuardRelease = null;
  }
}

// ── Run state helpers (DOM-facing; assigned stopBtn after init) ───────
// Refcounted so pair work and IB work can run concurrently without one
// endRun() hiding Stop or clearing the other's cancel/abort mid-flight.
function beginRun() {
  if (activeRuns === 0) {
    cancelled = false;
    activeAbort = new AbortController();
    acquireFreezeGuard();
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
    releaseFreezeGuard();
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

function doAuto(arg) {
  const [kind, newState] = autoApproveOutcome(autoApprove, arg || "");
  if (kind === "invalid") {
    print(`  Usage: ${yellow("/auto [on|off]")} (bare /auto toggles)`);
    return;
  }
  autoApprove = newState;
  if (kind === "on") {
    print(`  Auto-approve ${green("on")} — bulk y/n confirms are skipped. Target hits still ask.`);
  } else if (kind === "off") {
    print(`  Auto-approve ${yellow("off")} — runs over ${BULK_WARN} pairs ask y/n.`);
  } else if (kind === "show_on") {
    print(`  Auto-approve is ${green("on")}.`);
  } else {
    print(`  Auto-approve is ${yellow("off")}.`);
  }
}

function doRelay(arg) {
  const [kind, newState] = relayToggleOutcome(relayUserOn, arg || "");
  if (kind === "invalid") {
    print(`  Usage: ${yellow("/relay [on|off|status]")} (bare /relay toggles)`);
    return;
  }
  relayUserOn = newState;
  if (newState && relayReachable !== true) relayWarmup().catch(() => {});
  if (!newState) rateLimitEffective = 0; // off → drop the budget split
  const conn =
    relayReachable === true
      ? green("connected")
      : relayReachable === null
        ? yellow("warming up")
        : red("unreachable");
  const counters = `${green(String(relayHits))} served from hive, ${relayContributed} contributed, ${yellow(String(bountiesWorked))} bounties worked`;
  const extras = [];
  if (rateLimitEffective > 0) {
    extras.push(`budget split to ${rateLimitEffective}/min (other sessions on your IP)`);
  }
  if (cooling()) {
    extras.push(red(`429 cooldown until ~${new Date(cooldownUntil).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`));
  }
  const extra = extras.length ? "\n  " + extras.join("\n  ") : "";
  if (kind === "on") {
    print(`  Relay ${green("on")} (${conn}) — ${counters}.${extra}`);
  } else if (kind === "off") {
    print(`  Relay ${yellow("off")} — pairs go straight to neal.fun.`);
  } else if (kind === "show_on") {
    print(`  Relay is ${green("on")} (${conn}) — ${counters}.${extra}`);
  } else {
    print(`  Relay is ${yellow("off")}.`);
  }
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
  // score descending, pair-key tie-break), then cached hits promoted to
  // the front (they cost no rate-limit slot); same ordering as the CLI.
  if (pairs.length < 2) return pairs;
  return pairsFromBoundary(
    prioritizePairsBoundary(pairTuples(pairs), recipeIndex, [...pairCache.keys()])
  );
}

// ── Bulk pair processor ──────────────────────────────────────────────
// opts (optional):
//   onResult({ a, b, result, isNew, isTarget, extra, done, total })
//     — after a successful combine (storage/history already updated)
//   shouldPrint(ctx) → boolean
//     — whether to emit the default `[n/total] result` line; default: print
//       only when the result is new or hits the target
//   skipSummary — omit the final "Done: N new…" line (crawl gens use their own)
async function hiveSweep(pairs) {
  // Batch-lookup locally-missing pairs against the hive; merge hits.
  if (!relayActive()) return 0;
  const missing = pairs.filter(([a, b]) => !pairCache.has(pairKey(a.text, b.text)));
  if (!missing.length) return 0;
  const found = await relayLookup(missing.map(([a, b]) => [a.text, b.text]));
  if (!found) return 0;
  let merged = 0;
  for (const [a, b] of missing) {
    const key = pairKey(a.text, b.text);
    const v = found[key];
    if (v === undefined || pairCache.has(key)) continue;
    const cached = relayResultToCache(v);
    pairCache.set(key, cached);
    // Host parity: the Python _merge_hive_results records the recipe at
    // sweep time, so a swept-then-never-processed pair (e.g. cancelled run)
    // still lands in the recipe index identically in both hosts.
    if (cached) recordRecipe(cached.text, a.text, b.text);
    relayHits++;
    merged++;
  }
  return merged;
}

// Before spending a rate slot on a genuine miss, prefer the hive: while the
// pair isn't cached and no slot is free, re-sweep the tail (fleet-filled
// pairs land free) and wait a short, cancellable tick. Returns once the pair
// is cached or a slot is available, so the apiPair call never blocks on
// acquire. No-ops when the relay is down.
async function hiveAwareWait(pairEl, tail) {
  const resweepInterval = Number(hiveResweepIntervalMs());
  const tick = Number(hiveWaitTickMs());
  let lastResweep = 0;
  while (!cancelled && !cooling() && relayActive()) {
    const key = pairKey(pairEl[0].text, pairEl[1].text);
    if (pairCache.has(key)) return; // filled by the fleet → free
    if (rateChromeSnapshot().remaining >= 1) return; // a slot is available
    const now = Date.now();
    if (now - lastResweep >= resweepInterval) {
      const merged = await hiveSweep(tail);
      lastResweep = Date.now();
      if (merged) continue; // re-check: this pair may now be cached
    }
    try {
      await sleepCancellable(tick);
    } catch {
      return; // cancelled
    }
  }
}

async function runPairsInner(pairs, opts = {}) {
  if (pairs.length > 1) {
    // One hive sweep for the whole batch: anything any user has already
    // tried becomes a local cache hit before the first rate-limit slot is
    // spent, and the cache-first prioritization below promotes it.
    await hiveSweep(pairs);
  }
  if (relayActive()) {
    // Overflow beyond ~two windows of local budget goes on the bounty
    // board; idle users elsewhere fill the shared cache while we grind our
    // own share, and the periodic re-sweep below absorbs their results.
    const missNames = pairs
      .filter(([a, b]) => !pairCache.has(pairKey(a.text, b.text)))
      .map(([a, b]) => [a.text, b.text]);
    const snap = rateChromeSnapshot();
    const horizon = snap.remaining + snap.max;
    if (missNames.length > horizon) {
      const tail = missNames.slice(horizon, horizon + 500);
      relayFetch("/api/bounties", { pairs: tail }, 8000).then((d) => {
        if (!d) return;
        for (const [key, v] of Object.entries(d.results || {})) {
          if (!pairCache.has(key)) {
            const cached = relayResultToCache(v);
            pairCache.set(key, cached);
            const sep = key.indexOf("\0");
            if (cached && sep >= 0) recordRecipe(cached.text, key.slice(0, sep), key.slice(sep + 1));
            relayHits++;
          }
        }
        if (d.posted) print(dim(`  🐝 posted ${d.posted} bounties to the hive`));
      }).catch(() => {});
    }
  }
  pairs = prioritizePairs(pairs);
  let done = 0, newCount = 0, nothingCount = 0, errors = 0;
  const total = pairs.length;
  const onResult = opts.onResult;
  const shouldPrint = opts.shouldPrint;
  setLaneProgress("pair", 0, total);
  for (let i = 0; i < pairs.length; i++) {
    const [a, b] = pairs[i];
    if (cancelled) { print("  " + yellow("Cancelled.")); break; }
    if (cooling()) {
      print("  " + red(`429 cooldown — ${pairs.length - i} pairs skipped.`));
      break;
    }
    // Hive-aware wait: rather than block on a rate slot for a genuine miss,
    // drain the hive for the tail (fleet-filled pairs land as free cache
    // hits) and wait only until a slot frees or this pair gets filled.
    if (relayActive()) {
      await hiveAwareWait([a, b], pairs.slice(i));
      if (cancelled) { print("  " + yellow("Cancelled.")); break; }
      if (cooling()) { print("  " + red(`429 cooldown — ${pairs.length - i} pairs skipped.`)); break; }
    }
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
    if (bulkConfirmRequired(pairs.length, BULK_WARN, autoApprove)) {
      if (!(await confirmOrCancel([], { reason: `${pairs.length} pairs` }))) {
        print("  Cancelled.");
        return;
      }
    } else if (autoApprove && shouldBulkWarn(pairs.length, BULK_WARN)) {
      print("  " + dim(`Auto-approved ${pairs.length} pairs (/auto is on).`));
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
        if (!isConfirmAnswer(val) && runsLocal(val)) return; // main handler runs locals + pure scripts; y/n stays here
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


// ── Script driver (spec v0.6) ────────────────────────────────────────
// The kernel owns parse, static checks, and every pure evaluation; this
// driver walks only mutating spines and control flow, performing effects
// through the existing bulk machinery. Sets are tuples [name, emoji, first].
let scriptNewReg = []; // the [] register — session-global by design

function scriptFail(msg) {
  return new Error(msg);
}

function scriptEnvFlat(S) {
  const names = [], values = [];
  for (const frame of S.frames) {
    for (const bind of frame) { names.push(bind.name); values.push(bind.set); }
  }
  return [names, values];
}

function scriptBind(S, name, set) {
  const top = S.frames[S.frames.length - 1];
  for (const bind of top) {
    if (bind.name === name) { bind.set = set; return; }
  }
  top.push({ name, set });
}

function scriptSeed(S) {
  S.seedTick += 1;
  return (S.seedBase + S.seedTick * 7919) % 2147483648;
}

function scriptKernelEval(S, id) {
  const [names, values] = scriptEnvFlat(S);
  const [ok, set, err] = scriptEvalExprBoundary(
    S.nodes, S.kids, id, elementTuples(), recipeIndex, names, values, scriptNewReg, scriptSeed(S));
  if (!ok) throw scriptFail(err);
  return set;
}

function scriptKernelCond(S, id) {
  const [names, values] = scriptEnvFlat(S);
  const [ok, truth, err] = scriptEvalCondBoundary(
    S.nodes, S.kids, id, elementTuples(), recipeIndex, names, values, scriptNewReg, scriptSeed(S));
  if (!ok) throw scriptFail(err);
  return truth;
}

function scriptKernelNum(S, id) {
  const [names, values] = scriptEnvFlat(S);
  const [ok, value, err] = scriptEvalNumBoundary(
    S.nodes, S.kids, id, elementTuples(), recipeIndex, names, values, scriptNewReg, scriptSeed(S));
  if (!ok) throw scriptFail(err);
  return value;
}

// AST-node granularity: each completed mutating node overwrites the []
// register; active [ expr ] collectors accumulate.
function scriptRecordNews(S, news) {
  scriptNewReg = news;
  for (const collector of S.collectors) {
    for (const t of news) collector.push(t);
  }
}

function scriptTupleToEl(t) {
  return { text: t[0], emoji: t[1] || "", discovered: !!t[2] };
}

async function scriptCombinePair(S, aT, bT) {
  const result = await apiPair(aT[0], bT[0]);
  addElement(aT[0], aT[1], false);
  addElement(bT[0], bT[1], false);
  let products = [], news = [];
  if (result) {
    const isNew = addElement(result.text, result.emoji, result.discovered);
    recordRecipe(result.text, aT[0], bT[0]);
    history.push({ a: aT[0], b: bT[0], result: result.text });
    let extra = isNew ? " " + green("(new)") : "";
    const hit = isTargetHitKernel(targetElement, result.text || "");
    if (hit) extra += " " + bold(yellow("★ TARGET ★"));
    print(formatResult(scriptTupleToEl(aT), scriptTupleToEl(bT), result) + extra);
    products = [[result.text, result.emoji || "", !!result.discovered]];
    if (isNew) news = products.slice();
    if (hit) await acknowledgeTargetHit(aT[0], bT[0], result.text || "");
  } else {
    history.push({ a: aT[0], b: bT[0], result: "Nothing" });
    print(formatResult(scriptTupleToEl(aT), scriptTupleToEl(bT), null));
  }
  scriptRecordNews(S, news);
  return products;
}

// Bulk pairs through the existing pipeline (confirms, /auto, rate, target,
// pair-level error resilience) with product/new collection on top.
async function scriptRunPairs(S, objPairs, reason) {
  if (!objPairs.length) {
    print("  " + dim("0 pairs — nothing to combine."));
    return { products: [], news: [] };
  }
  if (bulkConfirmRequired(objPairs.length, BULK_WARN, autoApprove)) {
    if (!(await confirmOrCancel([], { reason }))) throw scriptFail("Cancelled");
  } else if (autoApprove && shouldBulkWarn(objPairs.length, BULK_WARN)) {
    print("  " + dim(`Auto-approved ${objPairs.length} pairs (/auto is on).`));
  }
  if (cancelled) throw scriptFail("Cancelled");
  const products = [], news = [];
  const seenP = new Set(), seenN = new Set();
  await runPairsInner(objPairs, {
    onResult: ({ result, isNew }) => {
      if (!result) return;
      if (!seenP.has(result.text)) {
        seenP.add(result.text);
        products.push([result.text, result.emoji || "", !!result.discovered]);
      }
      if (isNew && !seenN.has(result.text)) {
        seenN.add(result.text);
        news.push([result.text, result.emoji || "", !!result.discovered]);
      }
    },
  });
  if (cancelled) throw scriptFail("Cancelled");
  return { products, news };
}

function scriptObjPairs(rawPairs) {
  return pairsFromBoundary(rawPairs);
}

async function scriptEvalOperand(S, id) {
  return S.muts[id] ? await scriptEval(S, id) : scriptKernelEval(S, id);
}

async function scriptEval(S, id) {
  if (cancelled) throw scriptFail("Cancelled");
  if (!S.muts[id]) return scriptKernelEval(S, id);
  const [kind, a, b, c, sval] = S.nodes[id];
  if (kind === "assign") {
    const v = await scriptEvalOperand(S, a);
    scriptBind(S, sval, v);
    return v;
  }
  if (kind === "union") {
    let acc = [];
    for (const kid of S.kids[a]) {
      acc = scriptUnionTuples(acc, await scriptEvalOperand(S, kid));
    }
    return acc;
  }
  if (kind === "diff" || kind === "intersect" || kind === "canrec" || kind === "cantrec") {
    const L = await scriptEvalOperand(S, a);
    const R = await scriptEvalOperand(S, b);
    return scriptSetOpBoundary(kind, L, R, recipeIndex);
  }
  if (kind === "first") {
    const v = await scriptEvalOperand(S, a);
    return v.filter((t) => !!t[2]);
  }
  if (kind === "take" || kind === "sample" || kind === "shuffle") {
    // Mutating inner: host walks it, then the kernel slices/samples.
    const v = await scriptEvalOperand(S, a);
    if (kind === "take") return scriptTakeTuples(v, scriptKernelNum(S, b));
    const n = kind === "shuffle" ? v.length : scriptKernelNum(S, b);
    return scriptSampleTuples(v, n, scriptSeed(S));
  }
  if (kind === "newset") {
    const collector = [];
    S.collectors.push(collector);
    try {
      await scriptEvalOperand(S, a);
    } finally {
      S.collectors.pop();
    }
    return scriptUnionTuples(collector, []);
  }
  if (kind === "combine") {
    const L = await scriptEvalOperand(S, a);
    const R = await scriptEvalOperand(S, b);
    if (L.length !== 1 || R.length !== 1) {
      throw scriptFail(`+ combines single elements (left matched ${L.length}, right matched ${R.length}) — use , to collect or * to cross`);
    }
    return await scriptCombinePair(S, L[0], R[0]);
  }
  if (kind === "cross") {
    const L = await scriptEvalOperand(S, a);
    const R = await scriptEvalOperand(S, b);
    const pairs = scriptObjPairs(crossPairsBoundary(L, R));
    const { products, news } = await scriptRunPairs(S, pairs, `${pairs.length} pairs`);
    scriptRecordNews(S, news);
    return products;
  }
  if (kind === "permute") {
    const v = await scriptEvalOperand(S, a);
    const pairs = scriptObjPairs(permutePairsBoundary(v));
    const { products, news } = await scriptRunPairs(S, pairs, `${pairs.length} pairs`);
    scriptRecordNews(S, news);
    return products;
  }
  if (kind === "exhaust") {
    const v = await scriptEvalOperand(S, a);
    const pairs = scriptObjPairs(exhaustPairsBoundary(v, elementTuples()));
    const { products, news } = await scriptRunPairs(S, pairs, `${pairs.length} pairs`);
    scriptRecordNews(S, news);
    return products;
  }
  if (kind === "permutate") {
    // Permute rounds over a growing pool until a round adds nothing new.
    let pool = await scriptEvalOperand(S, a);
    let products = [], news = [];
    while (true) {
      if (cancelled) throw scriptFail("Cancelled");
      const pairs = scriptObjPairs(permutePairsBoundary(pool));
      if (!pairs.length) break;
      const round = await scriptRunPairs(S, pairs, `${pairs.length} pairs per round`);
      products = scriptUnionTuples(products, round.products);
      news = scriptUnionTuples(news, round.news);
      if (!round.news.length) break;
      pool = scriptUnionTuples(pool, round.news);
    }
    scriptRecordNews(S, news);
    return products;
  }
  // crawl: pool = L ∪ R, generations of untried pairs until a generation
  // adds nothing (mirrors doCrawl's kernel-driven loop).
  const L = await scriptEvalOperand(S, a);
  const R = await scriptEvalOperand(S, b);
  let pool = scriptUnionTuples(L, R);
  const triedKeys = [];
  let products = [], news = [];
  let genNum = 0;
  while (true) {
    if (cancelled) throw scriptFail("Cancelled");
    const [rawPairs, newKeys] = crawlGenerationPairsBoundary(pool, triedKeys);
    for (const k of newKeys) triedKeys.push(k);
    const pairs = scriptObjPairs(rawPairs);
    if (!pairs.length) break;
    genNum++;
    print("  " + dim(`Gen ${genNum}: ${pairs.length} pairs to try...`));
    const gen = await scriptRunPairs(S, pairs, `${pairs.length} pairs this generation`);
    products = scriptUnionTuples(products, gen.products);
    news = scriptUnionTuples(news, gen.news);
    const before = pool.length;
    pool = scriptUnionTuples(pool, gen.products);
    const grew = pool.length - before;
    print("  " + dim(`Gen ${genNum} done: ${grew} element${grew === 1 ? "" : "s"} joined the pool.`));
    if (grew === 0) break;
  }
  scriptRecordNews(S, news);
  return products;
}

async function scriptExecBody(S, id) {
  await scriptExecStmt(S, id);
}

// Loop bodies execute in the loop's own frame: a braced block does NOT get
// an extra child scope here, so its walrus bindings reach the condition.
async function scriptExecLoopBody(S, id) {
  const [kind, a] = S.nodes[id];
  if (kind === "block") {
    await scriptExecStmts(S, a);
    return;
  }
  await scriptExecStmt(S, id);
}

async function scriptExecStmts(S, kidsIdx) {
  for (const stmt of S.kids[kidsIdx]) {
    if (cancelled) throw scriptFail("Cancelled");
    await scriptExecStmt(S, stmt);
  }
}

async function scriptExecStmt(S, id) {
  if (cancelled) throw scriptFail("Cancelled");
  const [kind, a, b, c, sval] = S.nodes[id];
  if (kind === "block") {
    S.frames.push([]);
    try {
      await scriptExecStmts(S, a);
    } finally {
      S.frames.pop();
    }
    return;
  }
  if (kind === "assign") {
    const v = await scriptEvalOperand(S, a);
    scriptBind(S, sval, v);
    if (S.loopDepth === 0) {
      // Inside loops the ack would flood one line per iteration.
      print("  " + dim(`${esc(sval)} = ${v.length} element${v.length === 1 ? "" : "s"}`));
    }
    return;
  }
  if (kind === "foreach") {
    const set = await scriptEvalOperand(S, a);
    S.loopDepth++;
    try {
      for (const el of set) {
        // Yield so Stop clicks and UI events run even for pure bodies.
        await new Promise((r) => setTimeout(r, 0));
        if (cancelled) throw scriptFail("Cancelled");
        S.frames.push([{ name: sval, set: [el] }]);
        try {
          await scriptExecBody(S, b);
        } finally {
          S.frames.pop();
        }
      }
    } finally {
      S.loopDepth--;
    }
    return;
  }
  if (kind === "until" || kind === "while") {
    // A loop owns ONE scope shared by its body and condition: bindings made
    // by the body (braced or not) are visible to the test — the spec's
    // `{ n := [ ... ] } -> |n| < 2` idiom depends on it. Popped at exit.
    S.frames.push([]);
    S.loopDepth++;
    try {
      let iters = 0;
      if (kind === "until") {
        while (true) {
          await scriptExecLoopBody(S, a);
          iters++;
          // Pure bodies never await the network: yield so the Stop button
          // and UI events are not starved (stress-test BUG-1).
          await new Promise((r) => setTimeout(r, 0));
          if (cancelled) throw scriptFail("Cancelled");
          if (scriptKernelCond(S, b)) break;
        }
        print("  " + dim(`loop: condition met after ${iters} iteration${iters === 1 ? "" : "s"}`));
      } else {
        while (true) {
          await new Promise((r) => setTimeout(r, 0));
          if (cancelled) throw scriptFail("Cancelled");
          if (!scriptKernelCond(S, b)) break;
          await scriptExecLoopBody(S, a);
          iters++;
        }
        if (iters === 0) print("  " + dim("~ loop: condition false, body skipped"));
        else print("  " + dim(`~ loop: stopped after ${iters} iteration${iters === 1 ? "" : "s"}`));
      }
    } finally {
      S.frames.pop();
      S.loopDepth--;
    }
    return;
  }
  if (kind === "ternary") {
    const truth = scriptKernelCond(S, a);
    await scriptExecBody(S, truth ? b : c);
    return;
  }
  // Expression statement: pure ones echo their value /search-style.
  const v = await scriptEval(S, id);
  if (!S.muts[id]) {
    if (!v.length) print("  No matches found.");
    else for (const t of v) print("  " + formatElement(scriptTupleToEl(t)));
  }
}

async function runScript(source) {
  const [ok, nodes, kids, muts, err, pos] = scriptParse(source);
  if (!ok) {
    print("  " + red(`Script error: ${esc(err)}`));
    return;
  }
  const S = {
    nodes, kids, muts, frames: [[]], collectors: [], loopDepth: 0,
    // Host-supplied randomness: deterministic kernel, clock-seeded host.
    // The tick advances per kernel call so loop iterations resample.
    seedBase: Date.now() % 2147483648, seedTick: 0,
  };
  try {
    beginRun();
    const root = nodes.length - 1;
    await scriptExecStmts(S, nodes[root][1]);
  } catch (e) {
    if (!cancelled) print("  " + red("Script aborted: " + esc((e && e.message) || String(e))));
    else print("  " + yellow("Cancelled."));
  } finally {
    endRun();
  }
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

async function doLucky(count) {
  if (!Number.isFinite(count) || count <= 0) {
    print(`  Usage: ${yellow("/lucky [count]")} (count must be positive)`);
    return;
  }
  const tried = [...pairCache.keys()];
  const seed = Date.now() % 2147483648;
  const rawPairs = luckyPairsBoundary(elementTuples(), recipeIndex, tried, count, seed);
  const pairs = pairsFromBoundary(rawPairs);
  if (!pairs.length) {
    print("  " + yellow("No untried pairs found — the space may be exhausted."));
    return;
  }
  let note = "";
  if (pairs.length < count) note = dim(` (only ${pairs.length} untried found)`);
  print(`  Feeling lucky: ${bold(String(pairs.length))} random untried pair${pairs.length === 1 ? "" : "s"}...${note}`);
  await confirmAndRunPairs(pairs);
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
  print(`  Exhausting ${matches.length} element${matches.length === 1 ? "" : "s"} matching ${yellow(esc(query))} with all discoveries (${pairs.length} pair${pairs.length === 1 ? "" : "s"})...`);
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
  print(`  ${matches.length} element${matches.length === 1 ? "" : "s"} match, ${pairs.length} unique pair${pairs.length === 1 ? "" : "s"}:`);
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

      if (!confirmed && bulkConfirmRequired(pairs.length, BULK_WARN, autoApprove)) {
        if (!(await confirmOrCancel([], { reason: `${pairs.length} pairs per round` }))) {
          print("  Cancelled.");
          return;
        }
        confirmed = true;
      } else if (!confirmed && autoApprove && shouldBulkWarn(pairs.length, BULK_WARN)) {
        print("  " + dim(`Auto-approved ${pairs.length} pairs per round (/auto is on).`));
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
    else print(`  Permutate done after ${round} round${round === 1 ? "" : "s"}.`);
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
  print(`  ${pairs.length} unique pair${pairs.length === 1 ? "" : "s"}`);
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

async function doScriptFile() {
  print("  Select a .ice script file...");
  const file = await pickFile(".ice");
  if (!file || cancelled) { print("  " + yellow("Cancelled.")); return; }
  print(`  Running ${bold(esc(file.name))}...`);
  const source = await file.text();
  await runScript(source);
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
  print(`  ${yellow(String(missing.length))} element${missing.length === 1 ? "" : "s"} missing recipes. Fetching from Infinibrowser...`);
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
  print(`  ${yellow(String(missing.length))} element${missing.length === 1 ? "" : "s"} without recipes:`);
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
    ${cyan("/with <element> <query>")}     Combine element with all matching discoveries
    ${cyan("<query> * <query>")}           Cross-combine matches from both queries
    ${cyan("/cross <query> <query>")}    Cross-combine matches from both queries
    ${cyan("/permute <query>")}            Combine all matching elements with each other
    ${cyan("/permutate <query>")}          Permute repeatedly until no new discoveries
    ${cyan("/exhaust <query>")}            Each match combined with all discoveries
    ${cyan("/lucky [count]")}              Try random untried pairs (default 10)

  ${bold("Query syntax (/search, /with, /permute, /permutate, /cross, /exhaust, shorthands):")}
    substring                   Default: case-insensitive substring
    * ? []                      fnmatch wildcards (e.g. fire*, mu?)
    /pattern/                   Regex, case-insensitive (| alternation, \\d escapes)
    !<query>                    Exclude matches (e.g. !fire* = everything except fire*)
    !                           All elements (exclude nothing)
    ^<query>                    First discoveries only (e.g. ^fire* = new fire* matches)
    ^                           All first discoveries

  ${bold("Scripting (every non-slash line is a script):")}
    ${cyan("stmt ; stmt")}                 Run statements in sequence
    ${cyan("name := expr")}                Bind a set for this script run
    ${cyan("a* , b*")}                     Union   ${cyan("a* - b*")} difference   ${cyan("a* & b*")} intersect
    ${cyan("a* / b*")}                     Keep a* having a known recipe with b* (${cyan("%")} = lacking)
    ${cyan("(expr)*  (expr)**  (expr)!")}  Permute / permutate / exhaust the set
    ${cyan("(expr)100  (expr)100?  (expr)?")}  First 100 / random 100 / shuffle ((expr)(|x*|) = dynamic)
    ${cyan("[ expr ]")} / ${cyan("[]")}             New elements made by expr / by the last operation
    ${cyan("^(expr)")}                     First discoveries only
    ${cyan("set @ body")} / ${cyan("set @x body")}  For each element (as ${cyan("_")} or ${cyan("x")}) run body
    ${cyan("body -> cond")}                Run body, repeat until cond is true
    ${cyan("body ~ cond")}                 While cond is true, run body
    ${cyan("cond ? body : body")}          Conds: ${cyan("|expr|")} sizes, comparisons, ${cyan("&&")} ${cyan("||")}
    ${cyan('"exact name"')}              Quoted = exact element (spaces, commas, shadows)
    ${cyan("/script")}                     Run a saved .ice script file

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
    ${cyan("/auto [on|off]")}              Auto-approve bulk y/n confirms (bare /auto toggles)
    ${cyan("/relay [on|off|status]")}      Hive mind: shared pair cache with other users
                                (bare /relay toggles; on by default). Pairs anyone
                                has tried are served without spending your rate
                                limit; your fresh results are shared back.
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

function rateBarHtml(remaining, max, oldestFracMilli, fleetUsed = 0) {
  const [left, cyan, gold, dark] = rateBarSplitSegments(
    remaining, fleetUsed, max, oldestFracMilli, RATE_BAR_LEFT, RATE_BAR_RIGHT
  );
  // Purple next-slot wait · cyan remaining · honey ▒ fleet-spent · dark own.
  return (
    `<span class="ict-rate-bar ict-rate-bar-age">${left}</span>` +
    `<span class="ict-rate-bar ict-rate-bar-cap">${cyan}</span>` +
    `<span class="ict-rate-bar ict-rate-bar-fleet">${gold}</span>` +
    `<span class="ict-rate-bar ict-rate-bar-dark">${dark}</span>` +
    ` <span class="ict-rate-num">${remaining}/${max}</span>`
  );
}

function updateChrome() {
  if (!rateEl || !jobEl || !queueEl) return;

  // Permanent rate line: segmented bar + optional last pair (pair lane only).
  const { remaining, max, oldestFracMilli, fleetUsed } = rateChromeSnapshot();
  const rateNote = rateStatusNote(remaining);
  // One-shot pulse: the rate line is rebuilt every RATE_TICK_MS, so a
  // continuous CSS animation just resets before it ever peaks. Instead tag
  // the bee with `.pulse` only on the render where the hit count actually
  // grew — the freshly-mounted element plays the short animation once.
  const beePulse = relayHits > lastRenderedHits ? " pulse" : "";
  lastRenderedHits = relayHits;
  const hiveHtml = relayHits > 0
    ? ` <span class="ict-rate-sep">·</span> <span class="ict-rate-hive"><span class="ict-bee${beePulse}">🐝</span> +${relayHits}</span>`
    : "";
  const servingHtml = bountyProgress !== null
    ? ` <span class="ict-rate-sep">·</span> <span class="ict-rate-hive">🐝 serving</span>`
    : "";
  const coolHtml = cooling()
    ? ` <span class="ict-rate-sep">·</span> <span class="ict-rate-cool">429 cooldown ~${esc(new Date(cooldownUntil).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }))}</span>`
    : "";
  const noteHtml = rateNote
    ? ` <span class="ict-rate-sep">·</span> <span class="ict-rate-note">${esc(rateNote)}</span>`
    : "";
  const ratePrefix = `<span class="ict-rate-label">rate</span> ${rateBarHtml(remaining, max, oldestFracMilli, fleetUsed)}` + hiveHtml + servingHtml + coolHtml + noteHtml;
  if (currentPairCommand && lastPairA != null && lastPairB != null) {
    // Width-aware: measure leftover cells after painting the rate segment once.
    rateEl.innerHTML = ratePrefix + ` <span class="ict-rate-sep">·</span> <span class="ict-rate-pair"></span>`;
    const pairSpan = rateEl.querySelector(".ict-rate-pair");
    const totalPx = rateEl.clientWidth || 320;
    // Sum every rendered bar segment (age/cap/fleet/dark) plus the chrome
    // around it — segment count varies with hive state, so measure them all.
    let barW = 0;
    rateEl.querySelectorAll(".ict-rate-bar").forEach((el) => { barW += el.offsetWidth; });
    let chromeW = 0;
    rateEl.querySelectorAll(".ict-rate-label, .ict-rate-num, .ict-rate-sep, .ict-rate-note, .ict-rate-hive, .ict-rate-cool")
      .forEach((el) => { chromeW += el.offsetWidth; });
    const usedPx = barW + chromeW + 16;
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
  if (bountyProgress !== null) {
    // Serving the hive — mirror the CLI's prominent job row (rate-line tag
    // alone was too easy to miss).
    const [bk, bn] = bountyProgress;
    jobParts.push(`<div class="ict-job-row"><span class="ict-job-mark">🐝</span> <span class="ict-job-label">hive</span> <span class="ict-job-cmd">fulfilling bounties</span> <span class="ict-job-prog">${bk}/${bn}</span> <span class="ict-job-sep">·</span> <span class="ict-job-hint">any input pauses instantly</span></div>`);
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
  if (isKnownSlashCommand(line)) {
    const errorSegments = validateCommandLineSegments(line);
    if (errorSegments) {
      print(renderErrorSegments(errorSegments));
      return false;
    }
  } else if (!isLocalCommand(line)) {
    // Always-script REPL: parse (and static-check) before queueing so
    // errors surface immediately, and nothing runs on a broken script.
    const [ok, , , , err] = scriptParse(line);
    if (!ok) {
      if (line.trimStart().startsWith("/")) {
        // Slash-shaped but neither a known command nor a parseable script.
        print(`  Unknown command. Type ${yellow("/help")} for commands.`);
      } else {
        print("  " + red(`Script error: ${esc(err)}`));
      }
      return false;
    }
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
  if (kind === "lucky") {
    const arg = payload.trim();
    await doLucky(arg ? parseInt(arg, 10) : 10);
    return;
  }
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
  if ((rest = slashArgs(line, "/auto")) !== null) { doAuto(rest); return; }
  if ((rest = slashArgs(line, "/relay")) !== null) { doRelay(rest); return; }
  if ((rest = slashArgs(line, "/script")) !== null) { await doScriptFile(); return; }
  if ((rest = slashArgs(line, "/clear")) !== null) { output.innerHTML = ""; return; }
  if ((rest = slashArgs(line, "/unfilled")) !== null) { doUnfilled(); return; }

  if (!isKnownSlashCommand(line)) {
    await runScript(line);
    return;
  }
  const classified = classifyCommandLine(line);
  if (!classified) {
    const errorSegments = validateCommandLineSegments(line);
    if (errorSegments) print(renderErrorSegments(errorSegments));
    return;
  }
  await executeClassified(classified[0], classified[1], line);
}

async function dispatch(line) {
  // Pure, loop-free scripts run immediately, like /search — they cannot
  // touch the save and should not wait behind a running bulk command.
  if (runsLocal(line)) {
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
    #ict-rate .ict-rate-bar-fleet{color:#ffb300}
    #ict-rate .ict-rate-bar-dark{color:#33405e}
    #ict-rate .ict-rate-hive{color:#ffb300}
    #ict-rate .ict-rate-cool{color:#ff6b6b}
    #ict-rate .ict-bee{display:inline-block;transform-origin:60% 60%}
    #ict-rate .ict-bee.pulse{animation:ict-bee-pulse 0.3s ease-out}
    @keyframes ict-bee-pulse{0%{transform:scale(1)}45%{transform:scale(1.32);filter:drop-shadow(0 0 6px rgba(255,179,0,.9))}100%{transform:scale(1)}}
    @media (prefers-reduced-motion: reduce){#ict-rate .ict-bee.pulse{animation:none}}
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
    #ict-rate .ict-rate-note{color:#e6b450}
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
      if (e.key !== "Enter") return;
      const confirmVal = input.value.trim();
      if (!runsLocal(confirmVal) || isConfirmAnswer(confirmVal)) return;
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
    relayWarmup().catch(() => {});
    startBountyWorker();
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
