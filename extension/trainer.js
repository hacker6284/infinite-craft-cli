(function () {
  "use strict";

  // ── Singleton guard ──────────────────────────────────────────────────
  if (window.__ICTrainer) return;
  window.__ICTrainer = true;

  // ── Constants ────────────────────────────────────────────────────────
  const BASE_ELEMENTS = new Set(["Water", "Fire", "Wind", "Earth"]);
  const RATE_LIMIT = 60;
  const RATE_WINDOW = 60000;
  const BULK_WARN = 200;
  const MAX_QUERY_LENGTH = 512;
  const MAX_REGEX_BODY_LENGTH = 200;
  const MATCH_SCAN_BUDGET = 500; // ms (Python MATCH_SCAN_BUDGET=0.5s)
  const REGEX_ERROR_INVALID = "Invalid regex pattern";
  const REGEX_ERROR_COMPLEX = "Regex pattern too complex";
  const RE_NESTED_QUANTIFIER = /(\+|\*|\?|\{\d*,?\d*\})\s*(\+|\*|\?|\{)/;
  const RE_DELIMITED_REGEX = /\/[^/]+\//;

  // ── State ────────────────────────────────────────────────────────────
  const history = []; // [{a, b, result}]
  const pairCache = new Map(); // "A\0B" -> {text,emoji,discovered}
  const cmdHistory = [];
  let cmdHistoryIdx = -1;
  let cancelled = false;
  let running = false;
  let waitingForConfirm = false;

  // ── CSS ──────────────────────────────────────────────────────────────
  const style = document.createElement("style");
  style.textContent = `
    #ict-container{position:fixed;bottom:0;left:0;right:0;z-index:999999;font-family:'Menlo','Consolas','Monaco',monospace;font-size:13px;line-height:1.4}
    #ict-header{background:#0f3460;color:#e0e0e0;padding:4px 10px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;user-select:none}
    #ict-header span{font-weight:bold}
    #ict-body{background:#1a1a2e;color:#e0e0e0;display:flex;flex-direction:column}
    #ict-output{overflow-y:auto;max-height:300px;padding:6px 10px;white-space:pre-wrap;word-break:break-word}
    #ict-output div{margin:1px 0}
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
      <div id="ict-input-row">
        <span id="ict-prompt">craft&gt;</span>
        <input id="ict-input" autocomplete="off" spellcheck="false" placeholder="Type /help for commands">
        <button id="ict-stop">Stop</button>
      </div>
    </div>`;
  document.body.appendChild(container);

  const output = document.getElementById("ict-output");
  const input = document.getElementById("ict-input");
  const body = document.getElementById("ict-body");
  const toggle = document.getElementById("ict-toggle");
  const stopBtn = document.getElementById("ict-stop");

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
    waitingForConfirm = false;
    endRun();
  });

  function endRun() {
    running = false;
    try { stopBtn.style.display = "none"; } catch {}
  }

  function beginRun() {
    cancelled = false;
    running = true;
    try { stopBtn.style.display = "inline"; } catch {}
  }

  input.focus();

  // ── Output helpers ───────────────────────────────────────────────────
  function print(html) {
    const div = document.createElement("div");
    div.innerHTML = html;
    output.appendChild(div);
    output.scrollTop = output.scrollHeight;
  }
  function esc(s) { const d = document.createElement("span"); d.textContent = s; return d.innerHTML; }
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
  let _nameIndex = {};    // lowercase text -> item
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
      _nameIndex[item.text.toLowerCase()] = item;
      _idIndex[item.id] = item;
    }
  }

  function getAllElements() {
    return _items;
  }

  function getByName(name) {
    return _nameIndex[name.toLowerCase()] || null;
  }

  function resolveElement(name) {
    const el = getByName(name);
    if (el) return el;
    const titled = name.replace(/\b\w/g, c => c.toUpperCase());
    const el2 = getByName(titled);
    if (el2) return el2;
    return { text: name.trim().replace(/\b\w/g, c => c.toUpperCase()), emoji: "", discovered: false };
  }

  function addElement(text, emoji, discovered) {
    const existing = _nameIndex[text.toLowerCase()];
    if (existing) {
      // Update discovery flag if newly discovered
      if (discovered && !existing.discovered) {
        existing.discovered = true;
        putItem(existing);
      }
      return false;
    }
    const item = { id: _nextId++, saveId: _saveId, text, emoji: emoji || "" };
    if (discovered) item.discovered = true;
    _items.push(item);
    _nameIndex[text.toLowerCase()] = item;
    _idIndex[item.id] = item;
    putItem(item);
    return true;
  }

  async function removeElement(name) {
    const item = _nameIndex[name.toLowerCase()];
    if (!item || BASE_ELEMENTS.has(item.text)) return false;
    _items = _items.filter(i => i.id !== item.id);
    _allItems = _allItems.filter(i => i.id !== item.id);
    delete _nameIndex[item.text.toLowerCase()];
    delete _idIndex[item.id];
    await deleteItem(item.id);
    return true;
  }

  // ── Recipe index (name-based, rebuilt from game data) ────────────────
  let recipeIndex = {}; // {resultName: [[aName, bName], ...]}

  function rebuildRecipeIndex() {
    recipeIndex = {};
    for (const item of _items) {
      if (!item.recipes || !item.recipes.length) continue;
      for (const pair of item.recipes) {
        if (pair.length !== 2) continue;
        const a = _idIndex[pair[0]], b = _idIndex[pair[1]];
        if (!a || !b) continue;
        const key = item.text;
        if (!recipeIndex[key]) recipeIndex[key] = [];
        const sorted = [a.text, b.text].sort();
        const exists = recipeIndex[key].some(r => r[0] === sorted[0] && r[1] === sorted[1]);
        if (!exists) recipeIndex[key].push(sorted);
      }
    }
  }

  function recordRecipe(resultName, aName, bName) {
    const sorted = [aName, bName].sort();
    if (!recipeIndex[resultName]) recipeIndex[resultName] = [];
    const exists = recipeIndex[resultName].some(r => r[0] === sorted[0] && r[1] === sorted[1]);
    if (!exists) recipeIndex[resultName].push(sorted);
    // Persist to IndexedDB
    const resultItem = _nameIndex[resultName.toLowerCase()];
    const aItem = _nameIndex[aName.toLowerCase()];
    const bItem = _nameIndex[bName.toLowerCase()];
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

  // ── Rate limiter ─────────────────────────────────────────────────────
  const timestamps = [];
  function acquireRate() {
    return new Promise((resolve) => {
      function tryAcquire() {
        const now = Date.now();
        while (timestamps.length && timestamps[0] <= now - RATE_WINDOW) timestamps.shift();
        if (timestamps.length < RATE_LIMIT) {
          timestamps.push(now);
          resolve();
        } else {
          const wait = timestamps[0] + RATE_WINDOW - now + 10;
          setTimeout(tryAcquire, wait);
        }
      }
      tryAcquire();
    });
  }

  // ── API client ───────────────────────────────────────────────────────
  function pairKey(a, b) { return [a, b].sort().join("\0"); }

  async function apiPair(firstName, secondName) {
    const key = pairKey(firstName, secondName);
    if (pairCache.has(key)) return pairCache.get(key);
    await acquireRate();
    const url = `/api/infinite-craft/pair?first=${encodeURIComponent(firstName)}&second=${encodeURIComponent(secondName)}`;
    let resp;
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        resp = await fetch(url);
        if (resp.ok) break;
      } catch (e) { /* retry */ }
      if (attempt < 2) await new Promise(r => setTimeout(r, 1000 * Math.pow(2, attempt)));
    }
    if (!resp || !resp.ok) throw new Error("API request failed");
    const json = await resp.json();
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
      const result = await apiPair(a.text, b.text);
      if (result) {
        addElement(a.text, a.emoji, false);
        addElement(b.text, b.emoji, false);
        const isNew = addElement(result.text, result.emoji, result.discovered);
        recordRecipe(result.text, a.text, b.text);
        history.push({ a: a.text, b: b.text, result: result.text });
        print(formatResult(a, b, result) + (isNew ? " " + green("(new)") : ""));
      } else {
        history.push({ a: a.text, b: b.text, result: "Nothing" });
        print(formatResult(a, b, null));
      }
    } catch (e) {
      print("  " + red(`Error: ${e.message}`));
    }
  }

  // ── Query matching (parity with Python CLI) ───────────────────────────
  function parseQueryFilter(query) {
    let q = query.trim();
    let onlyNew = false;
    if (q.startsWith("!")) {
      onlyNew = true;
      q = q.slice(1);
    } else if (q.startsWith("^")) {
      onlyNew = true;
      q = q.slice(1);
    }
    return { pattern: q, onlyNew };
  }

  function isDelimitedRegex(pattern) {
    pattern = pattern.trim();
    return pattern.length >= 2 && pattern.startsWith("/") && pattern.endsWith("/");
  }

  function containsDelimitedRegex(text) {
    return RE_DELIMITED_REGEX.test(text);
  }

  function regexIsSafe(regexBody) {
    if (!regexBody || regexBody.length > MAX_REGEX_BODY_LENGTH) return false;
    if (regexBody.includes("|")) return false;
    if (RE_NESTED_QUANTIFIER.test(regexBody)) return false;
    if (/\([^)]*[+*?][^)]*\)[+*?{]/.test(regexBody)) return false;
    return true;
  }

  function regexSearch(pattern, name) {
    if (!regexIsSafe(pattern)) {
      return { matched: null, error: REGEX_ERROR_COMPLEX };
    }
    try {
      const re = new RegExp(pattern, "i");
      return { matched: re.test(name), error: null };
    } catch {
      return { matched: null, error: REGEX_ERROR_INVALID };
    }
  }

  function fnmatchToRegex(pattern) {
    let re = "^";
    for (let i = 0; i < pattern.length; i++) {
      const ch = pattern[i];
      if (ch === "*") {
        re += ".*";
      } else if (ch === "?") {
        re += ".";
      } else if (ch === "[") {
        let j = i + 1;
        let cls = "[";
        if (j < pattern.length && (pattern[j] === "!" || pattern[j] === "^")) {
          cls += "^";
          j++;
        }
        if (j < pattern.length && pattern[j] === "]") {
          cls += "\\]";
          j++;
        }
        while (j < pattern.length && pattern[j] !== "]") {
          cls += pattern[j];
          j++;
        }
        if (j >= pattern.length) {
          re += "\\[";
        } else {
          re += cls + "]";
          i = j;
        }
      } else {
        re += ch.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      }
    }
    return new RegExp(re + "$", "i");
  }

  function elementMatchesPattern(name, pattern) {
    pattern = pattern.trim();
    if (!pattern) return { matched: false, error: null };
    if (isDelimitedRegex(pattern)) {
      const regexBody = pattern.slice(1, -1);
      if (!regexBody) return { matched: false, error: null };
      const { matched, error } = regexSearch(regexBody, name);
      if (error) return { matched: false, error };
      return { matched, error: null };
    }
    const nameLower = name.toLowerCase();
    const patternLower = pattern.toLowerCase();
    if (/[*?[\]]/.test(patternLower)) {
      return { matched: fnmatchToRegex(patternLower).test(nameLower), error: null };
    }
    return { matched: nameLower.includes(patternLower), error: null };
  }

  function matchElements(query) {
    if (query.length > MAX_QUERY_LENGTH) {
      return { matches: [], error: `Query too long (max ${MAX_QUERY_LENGTH} characters)` };
    }
    const discoveries = getAllElements();
    const { pattern, onlyNew } = parseQueryFilter(query);
    if (!pattern.trim()) return { matches: [], error: null };

    const matches = [];
    let matchError = null;
    const deadline = Date.now() + MATCH_SCAN_BUDGET;
    for (const e of discoveries) {
      if (Date.now() > deadline) {
        return { matches: [], error: REGEX_ERROR_COMPLEX };
      }
      const { matched, error } = elementMatchesPattern(e.text, pattern);
      if (error) {
        matchError = error;
        break;
      }
      if (matched) matches.push(e);
    }
    if (matchError) return { matches: [], error: matchError };
    if (onlyNew) {
      return { matches: matches.filter(e => e.discovered), error: null };
    }
    return { matches, error: null };
  }

  function slashArgs(line, command) {
    if (line === command) return "";
    const prefix = command + " ";
    if (line.startsWith(prefix)) return line.slice(prefix.length);
    return null;
  }

  function splitOnFirstWhitespace(rest) {
    const i = rest.search(/\s/);
    if (i < 0) return null;
    const first = rest.slice(0, i).trim();
    const second = rest.slice(i).trim();
    if (!first || !second) return null;
    return [first, second];
  }

  function splitOnce(rest, delimiter) {
    const idx = rest.indexOf(delimiter);
    if (idx < 0) return null;
    const first = rest.slice(0, idx).trim();
    const second = rest.slice(idx + delimiter.length).trim();
    if (!first || !second) return null;
    return [first, second];
  }

  function parseTwoElements(rest) {
    rest = rest.trim();
    if (rest.includes(" + ")) return splitOnce(rest, " + ");
    return splitOnFirstWhitespace(rest);
  }

  function parseWithArgs(rest) {
    return splitOnFirstWhitespace(rest.trim());
  }

  function parseCrossQueries(rest) {
    rest = rest.trim();
    if (rest.includes(" * ")) return splitOnce(rest, " * ");
    if (containsDelimitedRegex(rest)) return null;
    return splitOnFirstWhitespace(rest);
  }

  // ── Bulk pair processor ──────────────────────────────────────────────
  async function runPairs(pairs) {
    let done = 0, newCount = 0, nothingCount = 0, errors = 0;
    const total = pairs.length;
    try {
      beginRun();
      for (const [a, b] of pairs) {
        if (cancelled) { print("  " + yellow("Cancelled.")); break; }
        try {
          const result = await apiPair(a.text, b.text);
          done++;
          if (result) {
            const isNew = addElement(result.text, result.emoji, result.discovered);
            recordRecipe(result.text, a.text, b.text);
            history.push({ a: a.text, b: b.text, result: result.text });
            if (isNew) {
              newCount++;
              print(`  ${dim(`[${done}/${total}]`)} ${formatResult(a, b, result)} ${green("(new)")}`);
            }
          } else {
            nothingCount++;
            history.push({ a: a.text, b: b.text, result: "Nothing" });
          }
        } catch (e) {
          done++;
          errors++;
        }
        // Yield to UI
        await new Promise(r => setTimeout(r, 0));
      }
    } finally {
      endRun();
    }
    print(`  Done: ${green(String(newCount))} new, ${dim(String(nothingCount))} nothing, ${errors ? red(String(errors)) + " errors" : "0 errors"} (${done}/${total})`);
  }

  async function confirmAndRunPairs(pairs) {
    if (pairs.length > BULK_WARN) {
      try {
        beginRun();
        print(`  ${yellow(`${pairs.length} pairs`)} — type ${bold("y")} or ${bold("yes")} to continue, anything else to cancel.`);
        const answer = await waitForInput();
        if (!["y", "yes"].includes(answer.toLowerCase())) { print("  Cancelled."); return; }
      } finally {
        endRun();
      }
    }
    print(`  Running ${bold(String(pairs.length))} combinations...`);
    await runPairs(pairs);
  }

  function waitForInput() {
    return new Promise((resolve) => {
      waitingForConfirm = true;
      function cleanup() {
        waitingForConfirm = false;
        try { input.removeEventListener("keydown", handler, true); } catch {}
      }
      function handler(e) {
        if (e.key === "Enter") {
          e.stopImmediatePropagation();
          const val = input.value.trim();
          input.value = "";
          cleanup();
          resolve(val);
        }
      }
      try {
        // Use capture to get this before the main handler
        input.addEventListener("keydown", handler, true);
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

  function doRecipe(name) {
    const el = getByName(name);
    if (!el) { print("  " + red("Element not found.")); return; }
    if (BASE_ELEMENTS.has(el.text)) { print(`  ${formatElement(el)} is a base element.`); return; }
    if (!recipeIndex[el.text] || !recipeIndex[el.text].length) {
      print("  " + yellow("No recipe known. Try /import or /fill."));
      return;
    }
    // BFS to find shortest path from base elements (or terminal constituents
    // that have no recipe entry of their own, e.g. leaves from /fill or /import).
    // NOTE: Keep this logic in sync with bookmarklet/trainer.js (and vice-versa).
    const visited = new Set(BASE_ELEMENTS);
    const layers = [];
    let found = false;
    for (let depth = 0; depth < 200 && !found; depth++) {
      const layer = [];
      for (const [resultName, pairs] of Object.entries(recipeIndex)) {
        if (visited.has(resultName)) continue;
        for (const [a, b] of pairs) {
          const aAvail = visited.has(a) || BASE_ELEMENTS.has(a) ||
                         !(recipeIndex[a] && recipeIndex[a].length);
          const bAvail = visited.has(b) || BASE_ELEMENTS.has(b) ||
                         !(recipeIndex[b] && recipeIndex[b].length);
          if (aAvail && bAvail) {
            layer.push({ result: resultName, a, b });
            break;
          }
        }
      }
      if (!layer.length) break;
      for (const step of layer) {
        visited.add(step.result);
        if (step.result === el.text) found = true;
      }
      layers.push(layer);
      if (found) break;
    }
    if (!found) { print("  " + yellow("Cannot trace full lineage — some intermediate recipes missing.")); return; }
    // Backtrack: only include steps needed for target
    const needed = new Set([el.text]);
    const steps = [];
    for (let i = layers.length - 1; i >= 0; i--) {
      for (const step of layers[i]) {
        if (needed.has(step.result)) {
          steps.unshift(step);
          if (!BASE_ELEMENTS.has(step.a)) needed.add(step.a);
          if (!BASE_ELEMENTS.has(step.b)) needed.add(step.b);
        }
      }
    }
    print(`  Recipe for ${formatElement(el)} (${bold(String(steps.length))} steps):`);
    for (let i = 0; i < steps.length; i++) {
      const s = steps[i];
      const aEl = resolveElement(s.a);
      const bEl = resolveElement(s.b);
      const rEl = resolveElement(s.result);
      print(`  ${dim(String(i + 1) + ".")} ${formatElement(aEl)} + ${formatElement(bEl)} = ${formatElement(rEl)}`);
    }
  }

  async function doExhaust(name) {
    const target = resolveElement(name);
    const all = getAllElements();
    const pairs = all.filter(e => e.text !== target.text).map(e => [target, e]);
    if (!pairs.length) {
      print(`  No other elements to combine with ${bold(esc(target.text))}.`);
      return;
    }
    print(`  Combining ${bold(esc(target.text))} with ${pairs.length} elements...`);
    await confirmAndRunPairs(pairs);
  }

  async function doCrawl(aName, bName) {
    const a = resolveElement(aName);
    const b = resolveElement(bName);
    print(`  Crawling from ${formatElement(a)} + ${formatElement(b)}...`);

    try {
      beginRun();
      // Initial combine
      let pool = new Set();
      const tried = new Set();
      const result = await apiPair(a.text, b.text);
      tried.add(pairKey(a.text, b.text));
      if (result) {
        addElement(a.text, a.emoji, false);
        addElement(b.text, b.emoji, false);
        pool.add(a.text);
        pool.add(b.text);
        const isNew = addElement(result.text, result.emoji, result.discovered);
        recordRecipe(result.text, a.text, b.text);
        history.push({ a: a.text, b: b.text, result: result.text });
        pool.add(result.text);
        print(`  ${formatResult(a, b, result)}${isNew ? " " + green("(new)") : ""}`);
      } else {
        print(formatResult(a, b, null));
        return;
      }

      let gen = 1;
      while (!cancelled) {
        const elements = [...pool].map(n => resolveElement(n));
        const pairs = [];
        for (let i = 0; i < elements.length; i++) {
          for (let j = i; j < elements.length; j++) {
            const key = pairKey(elements[i].text, elements[j].text);
            if (!tried.has(key)) { pairs.push([elements[i], elements[j]]); tried.add(key); }
          }
        }
        if (!pairs.length) { print("  " + dim("No more untried pairs.")); break; }
        print(`  ${dim(`Gen ${gen}:`)} ${pairs.length} pairs to try...`);
        let newInGen = 0;
        for (const [pa, pb] of pairs) {
          if (cancelled) break;
          try {
            const r = await apiPair(pa.text, pb.text);
            if (r) {
              const isNew = addElement(r.text, r.emoji, r.discovered);
              recordRecipe(r.text, pa.text, pb.text);
              history.push({ a: pa.text, b: pb.text, result: r.text });
              if (isNew && !pool.has(r.text)) {
                pool.add(r.text);
                newInGen++;
                print(`  ${formatResult(pa, pb, r)} ${green("(new)")}`);
              }
            }
          } catch { /* skip */ }
          await new Promise(r => setTimeout(r, 0));
        }
        print(`  ${dim(`Gen ${gen} done:`)} ${green(String(newInGen))} new elements.`);
        if (newInGen === 0) break;
        gen++;
      }
      if (cancelled) print("  " + yellow("Crawl cancelled."));
      print(`  Pool size: ${bold(String(pool.size))} elements.`);
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
    const pairs = [];
    for (let i = 0; i < matches.length; i++) {
      for (let j = i + 1; j < matches.length; j++) {
        pairs.push([matches[i], matches[j]]);
      }
    }
    print(`  ${matches.length} elements match, ${pairs.length} unique pairs:`);
    for (const m of matches) print(`    ${formatElement(m)}`);
    await confirmAndRunPairs(pairs);
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
    const seen = new Set();
    const pairs = [];
    for (const a of left) {
      for (const b of right) {
        if (a.text === b.text) continue;
        const key = pairKey(a.text, b.text);
        if (seen.has(key)) continue;
        seen.add(key);
        pairs.push([a, b]);
      }
    }
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
    const pairs = others.filter(o => o.text !== target.text).map(o => [target, o]);
    if (!pairs.length) { print(`  No other elements match: ${esc(query)}`); return; }
    print(`  Combining ${bold(esc(target.text))} with ${pairs.length} elements matching ${yellow(esc(query))}...`);
    await confirmAndRunPairs(pairs);
  }

  // Fetch with retry + backoff for 429s
  async function fetchRetry(url, maxRetries = 3) {
    for (let attempt = 0; attempt <= maxRetries; attempt++) {
      const resp = await fetch(url);
      if (resp.ok) return resp;
      if (resp.status === 429 && attempt < maxRetries) {
        const wait = Math.pow(2, attempt + 1) * 1000; // 2s, 4s, 8s
        await new Promise(r => setTimeout(r, wait));
        continue;
      }
      return resp;
    }
  }

  function processRecipeSteps(steps) {
    let count = 0;
    for (const step of steps) {
      const aText = step.a?.id || step.a?.text;
      const bText = step.b?.id || step.b?.text;
      const rText = step.result?.id || step.result?.text;
      const aEmoji = step.a?.emoji || "";
      const bEmoji = step.b?.emoji || "";
      const rEmoji = step.result?.emoji || "";
      if (aText) addElement(aText, aEmoji, false);
      if (bText) addElement(bText, bEmoji, false);
      if (rText) {
        addElement(rText, rEmoji, false);
        if (aText && bText) recordRecipe(rText, aText, bText);
        count++;
      }
    }
    return count;
  }

  function pickFile(accept) {
    return new Promise((resolve) => {
      const input = document.createElement("input");
      input.type = "file";
      if (accept) input.accept = accept;
      input.onchange = () => resolve(input.files[0] || null);
      input.click();
    });
  }

  async function doImportFile() {
    print("  Select a .ic save file...");
    const file = await pickFile(".ic");
    if (!file) { print("  " + yellow("Cancelled.")); return; }
    print(`  Reading ${bold(esc(file.name))}...`);
    try {
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
      // Build id-to-item lookup from the save file
      const idToItem = {};
      for (const item of items) idToItem[item.id] = item;
      let importedCount = 0, recipeCount = 0;
      for (const item of items) {
        const text = item.text;
        const emoji = item.emoji || "";
        const discovered = !!(item.discovery || item.discovered);
        const isNew = addElement(text, emoji, discovered);
        if (isNew) importedCount++;
        // Import recipes
        if (item.recipes) {
          for (const pair of item.recipes) {
            if (pair.length === 2 && idToItem[pair[0]] && idToItem[pair[1]]) {
              recordRecipe(text, idToItem[pair[0]].text, idToItem[pair[1]].text);
              recipeCount++;
            }
          }
        }
      }
      rebuildRecipeIndex();
      print(`  Loaded ${green(String(items.length))} elements (${importedCount} new) with ${recipeCount} recipes from ${bold(esc(file.name))}.`);
    } catch (e) {
      print("  " + red(`Error reading save file: ${e.message}`));
    }
  }

  async function doImport(name) {
    print(`  Importing ${bold(esc(name))} from Infinibrowser...`);
    try {
      const itemResp = await fetchRetry(`https://infinibrowser.wiki/api/item?id=${encodeURIComponent(name)}`);
      if (!itemResp.ok) { print("  " + red("Element not found on Infinibrowser.")); return; }
      const recipeResp = await fetchRetry(`https://infinibrowser.wiki/api/recipe?id=${encodeURIComponent(name)}`);
      if (!recipeResp.ok) { print("  " + red("No recipe found on Infinibrowser.")); return; }
      const recipeData = await recipeResp.json();
      const steps = recipeData.steps || recipeData.recipe || [];
      if (!steps.length) { print("  " + yellow("No recipe steps found.")); return; }
      const count = processRecipeSteps(steps);
      rebuildRecipeIndex();
      print(`  Imported ${green(String(count))} recipe steps for ${bold(esc(name))}.`);
    } catch (e) {
      print("  " + red(`Import failed: ${e.message}. CORS may be blocked — try the Python CLI instead.`));
    }
  }

  async function doFill() {
    const elements = getAllElements();
    const missing = elements.filter(e => !BASE_ELEMENTS.has(e.text) && (!recipeIndex[e.text] || !recipeIndex[e.text].length));
    if (!missing.length) { print("  All elements have recipes."); return; }
    print(`  ${yellow(String(missing.length))} elements missing recipes. Fetching from Infinibrowser...`);
    let filled = 0, errors = 0;
    try {
      beginRun();
      for (let i = 0; i < missing.length; i++) {
        if (cancelled) { print("  " + yellow("Fill cancelled.")); break; }
        const el = missing[i];
        try {
          const recipeResp = await fetchRetry(`https://infinibrowser.wiki/api/recipe?id=${encodeURIComponent(el.text)}`);
          if (recipeResp.ok) {
            const data = await recipeResp.json();
            const steps = data.steps || data.recipe || [];
            processRecipeSteps(steps);
            filled++;
          } else {
            errors++;
          }
        } catch { errors++; }
        if ((i + 1) % 10 === 0 || i === missing.length - 1) {
          print(`  ${dim(`[${i + 1}/${missing.length}]`)} ${green(String(filled))} filled, ${errors ? red(String(errors)) + " failed" : "0 failed"}`);
        }
        await new Promise(r => setTimeout(r, 500));
      }
    } finally {
      rebuildRecipeIndex();
      endRun();
    }
    print(`  Done: ${green(String(filled))} filled, ${errors ? red(String(errors)) + " failed" : "0 failed"} (${missing.length} total).`);
  }

  function doUnfilled() {
    const elements = getAllElements();
    const missing = elements.filter(e => !BASE_ELEMENTS.has(e.text) && (!recipeIndex[e.text] || !recipeIndex[e.text].length));
    if (!missing.length) { print("  All elements have recipes."); return; }
    print(`  ${yellow(String(missing.length))} elements without recipes:`);
    for (const el of missing) print("  " + formatElement(el));
  }

  function findOrphanCandidates() {
    const included = new Set(BASE_ELEMENTS);
    for (const [name, pairs] of Object.entries(recipeIndex)) {
      if (pairs && pairs.length) included.add(name);
    }
    let changed = true;
    while (changed) {
      changed = false;
      for (const name of included) {
        const pairs = recipeIndex[name];
        if (!pairs) continue;
        for (const [a, b] of pairs) {
          if (!included.has(a)) { included.add(a); changed = true; }
          if (!included.has(b)) { included.add(b); changed = true; }
        }
      }
    }
    return getAllElements().filter(e => !included.has(e.text));
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
      for (let i = 0; i < candidates.length; i++) {
        if (cancelled) { print("  " + yellow("Prune cancelled.")); break; }
        const el = candidates[i];
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
        await new Promise(r => setTimeout(r, 500));
      }
    } finally {
      rebuildIndexes();
      rebuildRecipeIndex();
      endRun();
    }
    print(`  Done: ${green(String(pruned))} pruned, ${kept} fillable on Infinibrowser (kept), ${skipped ? yellow(String(skipped)) + " skipped (API errors)" : "0 skipped"}.`);
  }

  async function doExport() {
    const exportItems = _items.map(item => {
      const exportItem = { id: item.id, text: item.text, emoji: item.emoji || "" };
      if (item.discovered) exportItem.discovery = true;
      if (item.recipes && item.recipes.length) exportItem.recipes = item.recipes;
      return exportItem;
    });
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
    ${cyan("/combine <el> + <el>")}        Same (also: /combine <el> <el>)

  ${bold("Crawl:")}
    ${cyan("<element> ++ <element>")}      Combine & crawl until no new discoveries
    ${cyan("/crawl <el> + <el>")}          Same as ++ (also: /crawl <el> <el>)

  ${bold("Bulk combine (query syntax below):")}
    ${cyan("<element> +| <query>")}        Combine element with all matching discoveries
    ${cyan("<element> + | <query>")}       Same as +| (spaced variant)
    ${cyan("/with <element> <query>")}     Same as +|
    ${cyan("<query> * <query>")}           Cross-combine matches from both queries
    ${cyan("/cross <query> * <query>")}    Same as * (also: /cross <q> <q>)
    ${cyan("/permute <query>")}            Combine all matching elements with each other
    ${cyan("/exhaust <element>")}          Combine element with all discoveries

  ${bold("Query syntax (/search, /with, /permute, /cross, shorthands):")}
    substring                   Default: case-insensitive substring
    * ? []                      fnmatch wildcards (e.g. fire*, mu?)
    /pattern/                   Regex, case-insensitive (no | alternation)
    !<query>                    First discoveries only (e.g. !fire*)
    ^<query>                    Legacy alias for !<query>

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
    ${cyan("/clear")}                      Clear output (browser only)
    ${cyan("/help")}                       Show this help`);
  }

  // ── Command dispatcher ───────────────────────────────────────────────
  async function dispatch(line) {
    if (running || waitingForConfirm) {
      print("  " + yellow("Busy — wait for current operation to finish."));
      return;
    }

    if (line.startsWith("/")) {
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
      if ((rest = slashArgs(line, "/permute")) !== null) {
        if (!rest) print("  Usage: /permute <query>");
        else await doPermute(rest);
        return;
      }
      if ((rest = slashArgs(line, "/import")) !== null) {
        if (!rest) await doImportFile();
        else if (rest.endsWith(".ic") || rest.includes("/") || rest.includes("\\")) await doImportFile();
        else await doImport(rest);
        return;
      }
      if ((rest = slashArgs(line, "/unfilled")) !== null) { doUnfilled(); return; }
      if ((rest = slashArgs(line, "/fill")) !== null) { await doFill(); return; }
      if ((rest = slashArgs(line, "/prune")) !== null) { await doPrune(); return; }
      if ((rest = slashArgs(line, "/export")) !== null) { await doExport(); return; }
      if ((rest = slashArgs(line, "/exhaust")) !== null) {
        if (!rest) print("  Usage: /exhaust <element>");
        else await doExhaust(rest);
        return;
      }
      if ((rest = slashArgs(line, "/combine")) !== null) {
        const parsed = parseTwoElements(rest);
        if (!parsed) print("  Usage: /combine <element> + <element>");
        else await doCombine(parsed[0], parsed[1]);
        return;
      }
      if ((rest = slashArgs(line, "/crawl")) !== null) {
        const parsed = parseTwoElements(rest);
        if (!parsed) print("  Usage: /crawl <element> + <element>");
        else await doCrawl(parsed[0], parsed[1]);
        return;
      }
      if ((rest = slashArgs(line, "/with")) !== null) {
        const parsed = parseWithArgs(rest);
        if (!parsed) print("  Usage: /with <element> <query>");
        else await doCombineWithQuery(parsed[0], parsed[1]);
        return;
      }
      if ((rest = slashArgs(line, "/cross")) !== null) {
        const parsed = parseCrossQueries(rest);
        if (!parsed) print("  Usage: /cross <query> * <query>");
        else await doCross(parsed[0], parsed[1]);
        return;
      }
      if ((rest = slashArgs(line, "/history")) !== null) { doHistory(); return; }
      if ((rest = slashArgs(line, "/clear")) !== null) { output.innerHTML = ""; return; }
      const spaceIdx = line.indexOf(" ");
      const cmd = spaceIdx > 0 ? line.slice(0, spaceIdx) : line;
      print(`  Unknown command: ${esc(cmd)}. Type ${yellow("/help")} for commands.`);
      return;
    }

    // Operator parsing (precedence: ++ > +| > * > +; spaced delimiters only)
    if (line.includes(" ++ ")) {
      const [a, b] = line.split(" ++ ", 2).map(s => s.trim());
      if (a && b) { await doCrawl(a, b); return; }
      print("  Usage: <element> ++ <element>");
      return;
    }
    if (/\+\s*\|/.test(line)) {
      const parts = line.split(/\+\s*\|/, 2);
      const name = parts[0].trim();
      const query = parts[1].trim();
      if (name && query) { await doCombineWithQuery(name, query); return; }
      print("  Usage: <element> +| <query>");
      return;
    }
    if (line.includes(" * ")) {
      const [left, right] = line.split(" * ", 2).map(s => s.trim());
      if (left && right) { await doCross(left, right); return; }
      print("  Usage: <query> * <query>");
      return;
    }
    if (line.includes(" + ")) {
      const [a, b] = line.split(" + ", 2).map(s => s.trim());
      if (a && b) { await doCombine(a, b); return; }
      print("  Usage: <element> + <element>");
      return;
    }

    print(`  Unknown input. Type ${yellow("/help")} for commands.`);
  }

  // ── Input handling ───────────────────────────────────────────────────
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !running && !waitingForConfirm) {
      const line = input.value.trim();
      if (!line) return;
      input.value = "";
      cmdHistory.push(line);
      cmdHistoryIdx = cmdHistory.length;
      print(cyan("craft&gt;") + " " + esc(line));
      dispatch(line).catch((err) => {
        // Last-ditch recovery: an uncaught error in a command must not leave the UI wedged.
        endRun();
        waitingForConfirm = false;
        print("  " + red("Error: " + (err && err.message || String(err))));
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
    output.innerHTML = "";
    print(bold(cyan("=== Infinite Craft Trainer ===")));
    print(`  Active save: ${bold(esc(saveName))} (id=${_saveId})`);
    print(`  ${green(String(_items.length))} elements loaded.`);
    const withRecipes = _items.filter(i => i.recipes && i.recipes.length).length;
    print(`  ${green(String(withRecipes))} recipes known.`);
    print(`  Type ${yellow("/help")} for commands.`);
    print("");
  }).catch(err => {
    print(red("Failed to load game data: " + err.message));
  });
})();
