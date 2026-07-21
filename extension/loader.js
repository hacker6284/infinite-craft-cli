// Thin loader: fetch hosted trainer and inject into page context (main world).
const TRAINER_URL =
  "https://hacker6284.github.io/infinite-craft-cli/trainer.min.js";
const FETCH_TIMEOUT_MS = 15000;
const INIT_TIMEOUT_MS = 3000;
const MAX_BYTES = 256 * 1024;
const MAX_RETRIES = 3;
const RETRY_BASE_MS = 1000;
const TRAINER_SENTINEL = "__ICTrainer";
const TRAINER_READY_EVENT = "ict-trainer-ready";

let loaderState = null;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function validatePayload(code, contentType) {
  if (!code || !code.trim()) {
    throw new Error("Empty trainer payload");
  }
  if (code.length > MAX_BYTES) {
    throw new Error(`Trainer payload too large (${code.length} bytes)`);
  }
  if (!contentType || !contentType.trim()) {
    throw new Error("Missing Content-Type header");
  }
  const normalizedType = contentType.toLowerCase();
  if (
    !normalizedType.includes("javascript") &&
    !normalizedType.includes("ecmascript")
  ) {
    throw new Error(`Unexpected Content-Type: ${contentType}`);
  }
  if (!code.includes(TRAINER_SENTINEL)) {
    throw new Error("Trainer payload missing sentinel");
  }
}

let bridgeReady = null;

function ensurePageBridge() {
  if (document.documentElement.dataset.ictPageBridge) {
    return Promise.resolve();
  }
  if (bridgeReady) {
    return bridgeReady;
  }
  bridgeReady = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = chrome.runtime.getURL("page-bridge.js");
    script.onload = () => {
      document.documentElement.dataset.ictPageBridge = "1";
      script.remove();
      resolve();
    };
    script.onerror = () => {
      bridgeReady = null;
      reject(new Error("Failed to load page bridge"));
    };
    (document.head || document.documentElement).appendChild(script);
  });
  return bridgeReady;
}

async function injectTrainer(code) {
  // Neal.fun CSP blocks content-script inline injection. Load a tiny
  // extension-origin bridge into the page world, then pass fetched code
  // through a DOM event the bridge executes there (IndexedDB access).
  await ensurePageBridge();
  document.dispatchEvent(
    new CustomEvent("ict-inject-trainer", { detail: code }),
  );
}

function waitForTrainerReady(timeoutMs) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const cleanup = () => {
      clearTimeout(timer);
      document.removeEventListener(TRAINER_READY_EVENT, onReady);
    };
    const onReady = () => {
      if (!document.getElementById("ict-container")) {
        return;
      }
      settled = true;
      cleanup();
      resolve();
    };
    const timer = setTimeout(() => {
      if (settled) {
        return;
      }
      cleanup();
      reject(new Error("Trainer did not initialize"));
    }, timeoutMs);
    document.addEventListener(TRAINER_READY_EVENT, onReady);
  });
}

function validateContentLength(contentLength) {
  if (!contentLength) {
    return;
  }
  const length = Number.parseInt(contentLength, 10);
  if (!Number.isFinite(length) || length > MAX_BYTES) {
    throw new Error(`Trainer payload too large (${contentLength} bytes)`);
  }
}

async function fetchTrainer() {
  const response = await fetch(TRAINER_URL, {
    cache: "no-store",
    signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
  });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  const contentType = response.headers.get("Content-Type") || "";
  validateContentLength(response.headers.get("Content-Length"));
  const code = await response.text();
  validatePayload(code, contentType);
  return code;
}

async function loadTrainer() {
  let lastError;

  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    try {
      if (attempt > 0) {
        await sleep(RETRY_BASE_MS * attempt);
      }
      const code = await fetchTrainer();
      try {
        const ready = waitForTrainerReady(INIT_TIMEOUT_MS);
        await injectTrainer(code);
        if (!document.getElementById("ict-container")) {
          await ready;
        }
        loaderState = "loaded";
        return;
      } catch (error) {
        lastError = error;
      }
    } catch (error) {
      lastError = error;
    }
  }

  loaderState = null;
  console.error(
    "[Infinite Craft Trainer] Failed to load trainer:",
    lastError,
  );
}

function maybeStartLoader() {
  if (document.getElementById("ict-container")) {
    loaderState = "loaded";
    return;
  }
  if (loaderState) {
    return;
  }
  loaderState = "loading";
  void loadTrainer();
}

maybeStartLoader();

window.addEventListener("pageshow", (event) => {
  if (!event.persisted) {
    return;
  }
  if (document.getElementById("ict-container")) {
    loaderState = "loaded";
    return;
  }
  if (loaderState === "loading") {
    loaderState = null;
  }
  maybeStartLoader();
});