// Privileged Infinibrowser hop. Content-script fetch is still subject to
// neal.fun CORS (Chrome: host_permissions do not lift CORS there). A
// service-worker fetch uses the extension origin, so the declared
// infinibrowser.wiki host permission applies.
const IB_API_HOST = "infinibrowser.wiki";
const FETCH_TIMEOUT_MS = 15000;

function isIbApiUrl(url) {
  try {
    const parsed = new URL(url);
    return (
      parsed.protocol === "https:" &&
      parsed.hostname === IB_API_HOST &&
      parsed.pathname.startsWith("/api/")
    );
  } catch {
    return false;
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || msg.type !== "ib-fetch") {
    return;
  }
  if (!isIbApiUrl(msg.url)) {
    sendResponse({ error: "Infinibrowser fetch URL is not allowed." });
    return;
  }
  fetch(msg.url, {
    credentials: "include",
    cache: "no-store",
    signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
  })
    .then(async (resp) => {
      const body = await resp.text();
      const headers = {};
      const contentType = resp.headers.get("content-type");
      if (contentType) {
        headers["content-type"] = contentType;
      }
      sendResponse({
        status: resp.status,
        statusText: resp.statusText,
        body,
        headers,
      });
    })
    .catch((error) => {
      sendResponse({
        error: error && error.message ? error.message : "Failed to fetch",
      });
    });
  return true;
});
