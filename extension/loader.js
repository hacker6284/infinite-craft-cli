// Inject trainer.js into the page context so it can access the game's IndexedDB
const script = document.createElement("script");
script.src = chrome.runtime.getURL("trainer.js");
script.onload = () => script.remove();
(document.head || document.documentElement).appendChild(script);
