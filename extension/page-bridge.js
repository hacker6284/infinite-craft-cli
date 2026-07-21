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
})();