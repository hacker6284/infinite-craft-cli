// ==UserScript==
// @name         Infinite Craft Trainer
// @namespace    https://github.com/hacker6284/infinite-craft-cli
// @version      1.0.0
// @description  CLI overlay for neal.fun Infinite Craft — combine, search, recipe, exhaust, crawl, and more
// @author       hacker6284
// @match        https://neal.fun/infinite-craft/*
// @icon         https://neal.fun/favicon.ico
// @grant        none
// @downloadURL  https://hacker6284.github.io/infinite-craft-cli/trainer.user.js
// @updateURL    https://hacker6284.github.io/infinite-craft-cli/trainer.user.js
// ==/UserScript==

/* The trainer code is loaded from the same origin to keep the userscript auto-updatable.
   If you prefer a fully self-contained script, replace the fetch below with the contents of trainer.js. */
const TRAINER_URL = 'https://hacker6284.github.io/infinite-craft-cli/trainer.min.js';
const FETCH_TIMEOUT_MS = 15000;

async function loadTrainer() {
  const response = await fetch(TRAINER_URL, {
    cache: 'no-store',
    signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
  });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  const code = await response.text();
  if (!code || !code.trim() || !code.includes('__ICTrainer')) {
    throw new Error('Invalid trainer payload');
  }
  const script = document.createElement('script');
  script.textContent = code;
  document.head.appendChild(script);
}

loadTrainer().catch((error) => {
  console.error('[Infinite Craft Trainer] Failed to load trainer:', error);
});