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
  const REGEX_TIMEOUT_MS = 20;
  const MAX_QUEUE_DEPTH = 50;
  const MAX_PERMUTATE_ROUNDS = 50;
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
  let confirmResolve = null;
  const commandQueue = [];
  let currentCommand = null;
  let activeAbort = null;

  // ── CSS ──────────────────────────────────────────────────────────────
  const style = document.createElement("style");
  style.textContent = `
    #ict-container{position:fixed;bottom:0;left:0;right:0;z-index:999999;font-family:'Menlo','Consolas','Monaco',monospace;font-size:13px;line-height:1.4}
    #ict-header{background:#0f3460;color:#e0e0e0;padding:4px 10px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;user-select:none}
    #ict-header span{font-weight:bold}
    #ict-body{background:#1a1a2e;color:#e0e0e0;display:flex;flex-direction:column}
    #ict-output{overflow-y:auto;max-height:300px;padding:6px 10px;white-space:pre-wrap;word-break:break-word}
    #ict-output div{margin:1px 0}
    #ict-queue{display:none;border-top:1px solid #0f3460;padding:4px 10px;background:#12182b;font-size:12px;max-height:80px;overflow-y:auto}
    #ict-queue .ict-queue-label{color:#ffeb3b;margin-bottom:2px}
    #ict-queue .ict-queue-item{margin:1px 0;opacity:.85}
    #ict-queue .ict-queue-running{color:#ffeb3b;margin-bottom:4px}
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
      <div id="ict-queue"></div>
      <div id="ict-input-row">
        <span id="ict-prompt">craft&gt;</span>
        <input id="ict-input" autocomplete="off" spellcheck="false" placeholder="Type /help for commands">
        <button id="ict-stop">Stop</button>
      </div>
    </div>`;
  document.body.appendChild(container);

  const output = document.getElementById("ict-output");
  const queueEl = document.getElementById("ict-queue");
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
    if (activeAbort) activeAbort.abort();
    if (waitingForConfirm && confirmResolve) {
      waitingForConfirm = false;
      const resolve = confirmResolve;
      confirmResolve = null;
      resolve("__cancelled__");
    }
  });

  function handleTrainerWheel(e) {
    if (collapsed || body.style.display === "none") return;
    output.scrollTop += e.deltaY;
    e.preventDefault();
    e.stopPropagation();
  }
  container.addEventListener("wheel", handleTrainerWheel, { passive: false });

  function beginRun() {
    cancelled = false;
    running = true;
    activeAbort = new AbortController();
    try { stopBtn.style.display = "inline"; } catch {}
  }

  function endRun() {
    running = false;
    activeAbort = null;
    try { stopBtn.style.display = "none"; } catch {}
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
  function sleepCancellable(ms) {
    return new Promise((resolve, reject) => {
      const step = 50;
      let elapsed = 0;
      function tick() {
        if (cancelled) { reject(new Error("Cancelled")); return; }
        if (elapsed >= ms) { resolve(); return; }
        const chunk = Math.min(step, ms - elapsed);
        elapsed += chunk;
        setTimeout(tick, chunk);
      }
      tick();
    });
  }

  function acquireRate() {
    return new Promise((resolve, reject) => {
      function tryAcquire() {
        if (cancelled) { reject(new Error("Cancelled")); return; }
        const now = Date.now();
        while (timestamps.length && timestamps[0] <= now - RATE_WINDOW) timestamps.shift();
        if (timestamps.length < RATE_LIMIT) {
          timestamps.push(now);
          resolve();
        } else {
          const wait = timestamps[0] + RATE_WINDOW - now + 10;
          sleepCancellable(wait).then(tryAcquire).catch(reject);
        }
      }
      tryAcquire();
    });
  }

  // ── API client ───────────────────────────────────────────────────────
  function pairKey(a, b) { return [a, b].sort().join("\0"); }

  async function apiPair(firstName, secondName) {
    if (cancelled) throw new Error("Cancelled");
    const key = pairKey(firstName, secondName);
    if (pairCache.has(key)) return pairCache.get(key);
    await acquireRate();
    if (cancelled) throw new Error("Cancelled");
    const url = `/api/infinite-craft/pair?first=${encodeURIComponent(firstName)}&second=${encodeURIComponent(secondName)}`;
    let resp;
    for (let attempt = 0; attempt < 3; attempt++) {
      if (cancelled) throw new Error("Cancelled");
      try {
        resp = await fetch(url, { signal: activeAbort ? activeAbort.signal : undefined });
        if (resp.ok) break;
      } catch (e) { /* retry */ }
      if (attempt < 2) await sleepCancellable(1000 * Math.pow(2, attempt));
    }
    if (cancelled) throw new Error("Cancelled");
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
      beginRun();
      const result = await apiPair(a.text, b.text);
      if (cancelled) return;
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
      if (!cancelled) print("  " + red(`Error: ${esc(e.message)}`));
    } finally {
      endRun();
    }
  }

  // ── Query matching (parity with Python CLI) ───────────────────────────
  function parseQueryFilter(query) {
    let q = query.trim();
    let exclude = false;
    let onlyNew = false;
    if (q.startsWith("!")) {
      exclude = true;
      q = q.slice(1);
    } else if (q.startsWith("^")) {
      onlyNew = true;
      q = q.slice(1);
    }
    return { pattern: q, exclude, onlyNew };
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
      const start = performance.now();
      const matched = re.test(name.slice(0, 512));
      if (performance.now() - start > REGEX_TIMEOUT_MS) {
        return { matched: null, error: REGEX_ERROR_COMPLEX };
      }
      return { matched, error: null };
    } catch {
      return { matched: null, error: REGEX_ERROR_INVALID };
    }
  }

  function fnmatchIsSafe(pattern) {
    if (!pattern || pattern.length > MAX_REGEX_BODY_LENGTH) return false;
    const wildcards = (pattern.match(/[*?]/g) || []).length;
    if (wildcards > 10) return false;
    if (/\*{2,}/.test(pattern) || /\*.*\*.*\*/.test(pattern)) return false;
    if (RE_NESTED_QUANTIFIER.test(pattern)) return false;
    return true;
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
      if (!fnmatchIsSafe(patternLower)) {
        return { matched: null, error: REGEX_ERROR_COMPLEX };
      }
      const start = performance.now();
      const matched = fnmatchToRegex(patternLower).test(nameLower.slice(0, 512));
      if (performance.now() - start > REGEX_TIMEOUT_MS) {
        return { matched: null, error: REGEX_ERROR_COMPLEX };
      }
      return { matched, error: null };
    }
    return { matched: nameLower.includes(patternLower), error: null };
  }

  function matchElements(query) {
    if (query.length > MAX_QUERY_LENGTH) {
      return { matches: [], error: `Query too long (max ${MAX_QUERY_LENGTH} characters)` };
    }
    const discoveries = getAllElements();
    const { pattern, exclude, onlyNew } = parseQueryFilter(query);
    if (!pattern.trim()) {
      if (exclude) return { matches: discoveries, error: null };
      if (onlyNew) return { matches: discoveries.filter(e => e.discovered), error: null };
      return { matches: [], error: null };
    }

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
      if (exclude) {
        if (!matched) matches.push(e);
      } else if (matched) {
        matches.push(e);
      }
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

  function splitTwoPositionalArgs(rest) {
    rest = rest.trim();
    if (!rest) return null;
    const tokens = [];
    let i = 0;
    const n = rest.length;
    while (i < n && tokens.length < 2) {
      while (i < n && /\s/.test(rest[i])) i++;
      if (i >= n) break;
      let token;
      if (rest[i] === "/") {
        const j = rest.indexOf("/", i + 1);
        if (j < 0) {
          let k = i;
          while (k < n && !/\s/.test(rest[k])) k++;
          token = rest.slice(i, k);
          i = k;
        } else {
          token = rest.slice(i, j + 1);
          i = j + 1;
        }
      } else {
        let j = i;
        while (j < n && !/\s/.test(rest[j])) j++;
        token = rest.slice(i, j);
        i = j;
      }
      token = token.trim();
      if (token) tokens.push(token);
    }
    if (tokens.length !== 2) return null;
    while (i < n && /\s/.test(rest[i])) i++;
    if (i < n) return null;
    return tokens;
  }

  function parseTwoElements(rest) {
    return splitOnFirstWhitespace(rest.trim());
  }

  function parseWithArgs(rest) {
    return splitOnFirstWhitespace(rest.trim());
  }

  function parseCrossQueries(rest) {
    return splitTwoPositionalArgs(rest);
  }

  function slashCombineCrawlOperatorError(rest, kind) {
    if (!rest.includes(" + ")) return null;
    const parts = rest.split(" + ", 2);
    const positional = `/${kind} ${parts[0].trim()} ${parts[1].trim()}`;
    return `  Slash /${kind} uses positional args, not +. Try ${yellow(rest.trim())} (shorthand) or ${yellow(positional)}.`;
  }

  function slashCrossOperatorError(rest) {
    if (!rest.includes(" * ")) return null;
    const parts = rest.split(" * ", 2);
    const positional = `/cross ${parts[0].trim()} ${parts[1].trim()}`;
    return `  Slash /cross uses positional args, not *. Try ${yellow(rest.trim())} (shorthand) or ${yellow(positional)}.`;
  }

  const API_SLASH_COMMANDS = [
    "/permute", "/permutate", "/import", "/fill", "/prune", "/export",
    "/exhaust", "/combine", "/crawl", "/with", "/cross",
  ];

  function isSlashCommandAttempt(line) {
    if (!line.startsWith("/")) return false;
    if (/^\/[^/]+\//.test(line)) return false;
    return /^\/\w/.test(line);
  }

  function classifyCommandLine(line) {
    line = line.trim();
    if (!line) return null;
    for (const cmd of API_SLASH_COMMANDS) {
      const rest = slashArgs(line, cmd);
      if (rest !== null) return [cmd.slice(1), rest];
    }
    if (isSlashCommandAttempt(line)) return null;
    if (/\+\s+\|/.test(line)) return ["bad+|", line];
    if (line.includes(" ++ ")) return ["++", line];
    if (line.includes("+|")) return ["+|", line];
    if (line.includes(" * ")) return ["*", line];
    if (line.includes(" + ") || / \+$/.test(line.trimEnd())) return ["+", line];
    return null;
  }

  function validateQueryAtEnqueue(query) {
    if (query.length > MAX_QUERY_LENGTH) {
      return `  Query too long (max ${MAX_QUERY_LENGTH} characters)`;
    }
    const { pattern } = parseQueryFilter(query);
    const q = pattern.trim();
    if (!isDelimitedRegex(q)) return null;
    const body = q.slice(1, -1);
    if (!regexIsSafe(body)) return `  ${REGEX_ERROR_COMPLEX}`;
    try {
      new RegExp(body, "i");
    } catch {
      return `  ${REGEX_ERROR_INVALID}`;
    }
    return null;
  }

  function slashCombineCrawlPipeError(rest) {
    if (/\+\s+\|/.test(rest)) {
      return `  Use <element> +| <query> (no space between + and |). Type ${yellow("/help")} for commands.`;
    }
    const parsed = parseTwoElements(rest);
    if (parsed && parsed[1].startsWith("|")) {
      return `  Use <element> +| <query> (no space between + and |). Type ${yellow("/help")} for commands.`;
    }
    return null;
  }

  function validateCommandLine(line) {
    const classified = classifyCommandLine(line);
    if (!classified) {
      if (isSlashCommandAttempt(line)) {
        const cmd = line.trim().split(/\s/)[0];
        return `  Unknown command: ${esc(cmd)}. Type ${yellow("/help")} for commands.`;
      }
      return `  Unknown input. Type ${yellow("/help")} for commands.`;
    }
    const [kind, payload] = classified;
    if (kind === "bad+|") {
      return `  Use <element> +| <query> (no space between + and |). Type ${yellow("/help")} for commands.`;
    }
    if (kind === "permute" || kind === "permutate" || kind === "exhaust") {
      if (!payload.trim()) return `  Usage: /${kind} <query>`;
      return validateQueryAtEnqueue(payload.trim());
    }
    if (kind === "import") {
      if (!payload.trim()) return "  Usage: /import <element>";
      return null;
    }
    if (kind === "export" || kind === "fill" || kind === "prune") {
      return null;
    }
    if (kind === "combine" || kind === "crawl") {
      const pipeErr = slashCombineCrawlPipeError(payload);
      if (pipeErr) return pipeErr;
      const opErr = slashCombineCrawlOperatorError(payload, kind);
      if (opErr) return opErr;
      if (!parseTwoElements(payload)) return `  Usage: /${kind} <element> <element>`;
      return null;
    }
    if (kind === "with") {
      const parsed = parseWithArgs(payload);
      if (!parsed) return "  Usage: /with <element> <query>";
      return validateQueryAtEnqueue(parsed[1]);
    }
    if (kind === "cross") {
      const opErr = slashCrossOperatorError(payload);
      if (opErr) return opErr;
      const parsed = parseCrossQueries(payload);
      if (!parsed) return "  Usage: /cross <query> <query>";
      return validateQueryAtEnqueue(parsed[0]) || validateQueryAtEnqueue(parsed[1]);
    }
    if (kind === "++") {
      const parts = payload.split(" ++ ", 2);
      if (!parts[0].trim() || !parts[1].trim()) return "  Usage: <element> ++ <element>";
      return null;
    }
    if (kind === "+|") {
      const parts = payload.split("+|", 2);
      if (!parts[0].trim() || !parts[1].trim()) return "  Usage: <element> +| <query>";
      return validateQueryAtEnqueue(parts[1].trim());
    }
    if (kind === "*") {
      const parts = payload.split(" * ", 2);
      if (!parts[0].trim() || !parts[1].trim()) return "  Usage: <query> * <query>";
      return validateQueryAtEnqueue(parts[0].trim()) || validateQueryAtEnqueue(parts[1].trim());
    }
    if (kind === "+") {
      const parts = payload.includes(" + ")
        ? payload.split(" + ", 2)
        : [payload.trimEnd().replace(/ \+$/, ""), ""];
      if (!parts[0].trim() || !parts[1].trim()) return "  Usage: <element> + <element>";
      return null;
    }
    return null;
  }

  // ── Bulk pair processor ──────────────────────────────────────────────
  async function runPairsInner(pairs) {
    let done = 0, newCount = 0, nothingCount = 0, errors = 0;
    const total = pairs.length;
    for (const [a, b] of pairs) {
      if (cancelled) { print("  " + yellow("Cancelled.")); break; }
      try {
        const result = await apiPair(a.text, b.text);
        if (cancelled) { print("  " + yellow("Cancelled.")); break; }
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
        if (cancelled) { print("  " + yellow("Cancelled.")); break; }
        done++;
        errors++;
      }
      await new Promise(r => setTimeout(r, 0));
    }
    if (!cancelled) {
      print(`  Done: ${green(String(newCount))} new, ${dim(String(nothingCount))} nothing, ${errors ? red(String(errors)) + " errors" : "0 errors"} (${done}/${total})`);
    }
  }

  async function confirmAndRunPairs(pairs) {
    try {
      beginRun();
      if (pairs.length > BULK_WARN) {
        print(`  ${yellow(`${pairs.length} pairs`)} — type ${bold("y")} or ${bold("yes")} to continue, anything else to cancel.`);
        const answer = await waitForInput();
        if (cancelled || answer === "__cancelled__" || !["y", "yes"].includes(answer.toLowerCase())) {
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

  function waitForInput() {
    return new Promise((resolve) => {
      waitingForConfirm = true;
      confirmResolve = resolve;
      function cleanup() {
        waitingForConfirm = false;
        confirmResolve = null;
        try { input.removeEventListener("keydown", handler, true); } catch {}
      }
      function handler(e) {
        if (e.key === "Enter") {
          const val = input.value.trim();
          if (isLocalCommand(val)) return;
          e.stopImmediatePropagation();
          input.value = "";
          const answer = val.toLowerCase();
          if (answer === "y" || answer === "yes" || answer === "n" || answer === "no" || answer === "") {
            cleanup();
            resolve(val);
          } else {
            tryEnqueue(val);
          }
        }
      }
      try {
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
    // NOTE: Keep this logic in sync with extension/trainer.js (and vice-versa).
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

  async function doExhaust(query) {
    const { matches, error } = matchElements(query);
    if (error) { print("  " + red(error)); return; }
    if (!matches.length) { print(`  No elements match: ${esc(query)}`); return; }

    const all = getAllElements();
    const seen = new Set();
    const pairs = [];
    for (const target of matches) {
      for (const other of all) {
        if (other.text === target.text) continue;
        const key = pairKey(target.text, other.text);
        if (seen.has(key)) continue;
        seen.add(key);
        pairs.push([target, other]);
      }
    }
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
      // Initial combine
      let pool = new Set();
      const tried = new Set();
      const result = await apiPair(a.text, b.text);
      if (cancelled) { print("  " + yellow("Crawl cancelled.")); return; }
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
          } catch (e) {
            if (cancelled) break;
          }
          await new Promise(r => setTimeout(r, 0));
        }
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

        const pairs = [];
        for (let i = 0; i < matches.length; i++) {
          for (let j = i + 1; j < matches.length; j++) {
            pairs.push([matches[i], matches[j]]);
          }
        }
        print(`  ${dim(`--- Round ${round}:`)} ${matches.length} elements, ${pairs.length} pairs ---`);

        if (!confirmed && pairs.length > BULK_WARN) {
          print(`  ${yellow(`${pairs.length} pairs per round`)} — type ${bold("y")} or ${bold("yes")} to continue.`);
          const answer = await waitForInput();
          if (cancelled || answer === "__cancelled__" || !["y", "yes"].includes(answer.toLowerCase())) {
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
      if (cancelled) throw new Error("Cancelled");
      const resp = await fetch(url, { signal: activeAbort ? activeAbort.signal : undefined });
      if (resp.ok) return resp;
      if (resp.status === 429 && attempt < maxRetries) {
        const wait = Math.pow(2, attempt + 1) * 1000;
        await sleepCancellable(wait);
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
      // Build id-to-item lookup from the save file
      const idToItem = {};
      for (const item of items) idToItem[item.id] = item;
      let importedCount = 0, recipeCount = 0;
      for (const item of items) {
        if (cancelled) break;
        const text = item.text;
        const emoji = item.emoji || "";
        const discovered = !!(item.discovery || item.discovered);
        const isNew = addElement(text, emoji, discovered);
        if (isNew) importedCount++;
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
      const count = processRecipeSteps(steps);
      rebuildRecipeIndex();
      print(`  Imported ${green(String(count))} recipe steps for ${bold(esc(name))}.`);
    } catch (e) {
      if (!cancelled) print("  " + red(`Import failed: ${esc(e.message)}. CORS may be blocked — try the Python CLI instead.`));
    } finally {
      endRun();
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
        await sleepCancellable(500);
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
    /pattern/                   Regex, case-insensitive (no | alternation)
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
    ${cyan("/clear")}                      Clear output (browser only)
    ${cyan("/help")}                       Show this help`);
  }

  function isLocalCommand(line) {
    if (line === "/help" || line === "/list" || line === "/history" || line === "/clear") return true;
    if (line === "/unfilled" || line.startsWith("/unfilled ")) return true;
    if (line === "/search" || line.startsWith("/search ")) return true;
    if (line === "/recipe" || line.startsWith("/recipe ")) return true;
    return false;
  }

  function updateQueueDisplay() {
    if (!currentCommand && !commandQueue.length) {
      queueEl.style.display = "none";
      queueEl.innerHTML = "";
      return;
    }
    queueEl.style.display = "block";
    let html = "";
    if (currentCommand) {
      html += `<div class="ict-queue-running">Running: ${esc(currentCommand)}</div>`;
    }
    if (commandQueue.length) {
      html += `<div class="ict-queue-label">Queue:</div>`;
      for (const cmd of commandQueue) {
        html += `<div class="ict-queue-item">${esc(cmd)}</div>`;
      }
    }
    queueEl.innerHTML = html;
  }

  function enqueueCommand(line) {
    const deferred = queueWorkerRunning || currentCommand !== null || waitingForConfirm;
    commandQueue.push(line);
    updateQueueDisplay();
    if (deferred) print("  " + dim(`Queued: ${esc(line)}`));
    ensureQueueWorker();
  }

  function tryEnqueue(line) {
    const error = validateCommandLine(line);
    if (error) {
      print(error);
      return false;
    }
    if (line === currentCommand || commandQueue.includes(line)) {
      print("  " + dim("Already queued."));
      return false;
    }
    if (commandQueue.length >= MAX_QUEUE_DEPTH) {
      print("  " + yellow(`Queue full (max ${MAX_QUEUE_DEPTH}).`));
      return false;
    }
    enqueueCommand(line);
    return true;
  }

  let queueWorkerRunning = false;

  async function ensureQueueWorker() {
    if (queueWorkerRunning) return;
    queueWorkerRunning = true;
    try {
      while (commandQueue.length) {
        const line = commandQueue.shift();
        updateQueueDisplay();
        currentCommand = line;
        updateQueueDisplay();
        cancelled = false;
        try {
          await executeCommand(line);
        } catch (err) {
          endRun();
          waitingForConfirm = false;
          confirmResolve = null;
          print("  " + red("Error: " + esc(err && err.message || String(err))));
        }
        currentCommand = null;
        updateQueueDisplay();
      }
    } finally {
      queueWorkerRunning = false;
    }
  }

  // ── Command dispatcher ───────────────────────────────────────────────
  async function executeClassified(kind, payload, line) {
    if (kind === "permute") {
      if (!payload.trim()) print("  Usage: /permute <query>");
      else await doPermute(payload.trim());
      return;
    }
    if (kind === "permutate") {
      if (!payload.trim()) print("  Usage: /permutate <query>");
      else await doPermutate(payload.trim());
      return;
    }
    if (kind === "import") {
      if (!payload.trim()) await doImportFile();
      else if (payload.endsWith(".ic") || payload.includes("/") || payload.includes("\\")) await doImportFile();
      else await doImport(payload.trim());
      return;
    }
    if (kind === "fill") { await doFill(); return; }
    if (kind === "prune") { await doPrune(); return; }
    if (kind === "export") { await doExport(); return; }
    if (kind === "exhaust") {
      if (!payload.trim()) print("  Usage: /exhaust <query>");
      else await doExhaust(payload.trim());
      return;
    }
    if (kind === "combine" || kind === "crawl") {
      const pipeErr = slashCombineCrawlPipeError(payload);
      if (pipeErr) { print(pipeErr); return; }
      const opErr = slashCombineCrawlOperatorError(payload, kind);
      if (opErr) { print(opErr); return; }
      const parsed = parseTwoElements(payload);
      if (!parsed) print(`  Usage: /${kind} <element> <element>`);
      else if (kind === "combine") await doCombine(parsed[0], parsed[1]);
      else await doCrawl(parsed[0], parsed[1]);
      return;
    }
    if (kind === "with") {
      const parsed = parseWithArgs(payload);
      if (!parsed) print("  Usage: /with <element> <query>");
      else await doCombineWithQuery(parsed[0], parsed[1]);
      return;
    }
    if (kind === "cross") {
      const opErr = slashCrossOperatorError(payload);
      if (opErr) { print(opErr); return; }
      const parsed = parseCrossQueries(payload);
      if (!parsed) print("  Usage: /cross <query> <query>");
      else await doCross(parsed[0], parsed[1]);
      return;
    }
    if (kind === "++") {
      const [a, b] = line.split(" ++ ", 2).map(s => s.trim());
      if (a && b) await doCrawl(a, b);
      else print("  Usage: <element> ++ <element>");
      return;
    }
    if (kind === "bad+|") {
      print(`  Use <element> +| <query> (no space between + and |). Type ${yellow("/help")} for commands.`);
      return;
    }
    if (kind === "+|") {
      const parts = line.split("+|", 2);
      const name = parts[0].trim();
      const query = parts[1].trim();
      if (name && query) await doCombineWithQuery(name, query);
      else print("  Usage: <element> +| <query>");
      return;
    }
    if (kind === "*") {
      const [left, right] = line.split(" * ", 2).map(s => s.trim());
      if (left && right) await doCross(left, right);
      else print("  Usage: <query> * <query>");
      return;
    }
    if (kind === "+") {
      const parts = line.includes(" + ")
        ? line.split(" + ", 2).map(s => s.trim())
        : [line.trimEnd().replace(/ \+$/, "").trim(), ""];
      if (parts[0] && parts[1]) await doCombine(parts[0], parts[1]);
      else print("  Usage: <element> + <element>");
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
    if ((rest = slashArgs(line, "/clear")) !== null) { output.innerHTML = ""; return; }
    if ((rest = slashArgs(line, "/unfilled")) !== null) { doUnfilled(); return; }

    const classified = classifyCommandLine(line);
    if (!classified) {
      const error = validateCommandLine(line);
      print(error);
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

  // ── Input handling ───────────────────────────────────────────────────
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      const line = input.value.trim();
      if (!line) return;
      if (waitingForConfirm && !isLocalCommand(line)) return;
      input.value = "";
      cmdHistory.push(line);
      cmdHistoryIdx = cmdHistory.length;
      print(cyan("craft&gt;") + " " + esc(line));
      dispatch(line).catch((err) => {
        endRun();
        waitingForConfirm = false;
        confirmResolve = null;
        currentCommand = null;
        updateQueueDisplay();
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
    print(red("Failed to load game data: " + esc(err.message)));
  });
})();
