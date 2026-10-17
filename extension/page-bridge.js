// Runs in the page (main) world via chrome-extension:// script src.
// Neal.fun CSP allows extension-origin scripts but blocks inline script tags.
(function () {
  if (window.__ICTPageBridge) return;
  window.__ICTPageBridge = true;

  document.addEventListener("ict-inject-trainer", (event) => {
    const code = event.detail;
    if (typeof code !== "string" || !code.includes("__ICTrainer")) {
      return;
    }
    // Extension-origin scripts may use eval in the page world; inline
    // script tags are blocked by neal.fun's nonce-only CSP.
    (0, eval)(code);
  });

  // Infinibrowser /api hops go through the content-script loader so Chrome
  // can attach this profile's infinibrowser.wiki cookies (Cloudflare
  // clearance). The page cannot read those cookies on a neal.fun fetch.
  let ibSeq = 0;
  const pending = new Map();
  const IB_FETCH_TIMEOUT_MS = 15000;

  window.addEventListener("message", (event) => {
    if (event.source !== window) return;
    const data = event.data;
    if (!data || data.source !== "ict-loader" || data.type !== "ib-fetch-result") {
      return;
    }
    const waiter = pending.get(data.id);
    if (!waiter) return;
    pending.delete(data.id);
    clearTimeout(waiter.timer);
    if (data.error) {
      waiter.reject(new TypeError(data.error));
      return;
    }
    waiter.resolve(
      new Response(data.body, {
        status: data.status,
        statusText: data.statusText || "",
        headers: data.headers || {},
      }),
    );
  });

  window.__ICTIbFetch = function (url) {
    return new Promise((resolve, reject) => {
      const id = "ib-" + ++ibSeq;
      const timer = setTimeout(() => {
        if (!pending.has(id)) return;
        pending.delete(id);
        reject(new TypeError("Infinibrowser fetch timed out"));
      }, IB_FETCH_TIMEOUT_MS);
      pending.set(id, { resolve, reject, timer });
      window.postMessage({ source: "ict-page", type: "ib-fetch", id, url }, "*");
    });
  };
})();