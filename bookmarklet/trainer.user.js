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
fetch('https://hacker6284.github.io/infinite-craft-cli/trainer.js')
  .then(r => r.text())
  .then(code => {
    const script = document.createElement('script');
    script.textContent = code;
    document.head.appendChild(script);
  });
