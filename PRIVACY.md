# Privacy Policy for Infinite Craft Trainer

**Last updated:** June 17, 2026

## Overview

Infinite Craft Trainer is a browser extension that adds a command-line interface to the Infinite Craft game at neal.fun. This privacy policy explains how the extension handles user data.

## Data Collection

**We do not collect any data.**

The extension:
- Does NOT collect personal information
- Does NOT track usage or analytics
- Does NOT send your game data, discoveries, or other user-specific information to external servers
- Does NOT use cookies or tracking technologies

The extension does make a read-only HTTP request to **hacker6284.github.io** (GitHub Pages) on each visit to neal.fun/infinite-craft to download the public trainer script. That request carries no user-specific or game-specific data — only a standard URL fetch for the open-source `trainer.min.js` file (same script used by the web trainer page and userscript).

## Data Storage

All data is stored locally in your browser:
- Your discovered elements are stored in your browser's IndexedDB (this is the game's own storage)
- No data is sent to us or any third party

## Permissions

The extension requires:

- **`neal.fun/infinite-craft/*`** — Read your discovered elements from the game's local database, call the game's API to combine elements (the same API the game itself uses), and display the CLI overlay on the game page.
- **`hacker6284.github.io/*`** — Read-only access to download the public trainer script from GitHub Pages. No user data is sent in this request.

## Third-Party Services

The extension communicates with:
- **hacker6284.github.io** — Hosts the trainer script the extension loads on each page visit (public, read-only download; no user data sent). The extension uses `cache: 'no-store'` so your browser does not serve a stale cached copy of the script.
- **neal.fun** — The official Infinite Craft game server, for element combinations
- **Infinibrowser** (infinibrowser.com) — Optional, only when you explicitly use the `/import` or `/fill` commands to look up recipes

## Remote Script Trust Model

The Chrome extension is intentionally a thin loader: it downloads and executes `trainer.min.js` from GitHub Pages so trainer updates can ship without a Chrome Web Store review. This means:

- **You trust the GitHub Pages deployment** (`hacker6284.github.io/infinite-craft-cli/`) to serve the authentic open-source trainer built from this repository's `bookmarklet/trainer.src.mjs` (bundled by `//bookmarklet:trainer_min_js`).
- **The loader runs in the extension's isolated content-script world**; only the fetched trainer code executes in the page context (required for IndexedDB access). Loader state is tracked in the content-script scope, not in page-writable DOM attributes.
- **Init verification uses a cross-world handshake**: the trainer dispatches an `ict-trainer-ready` custom event after creating its UI; the loader listens for this event (not DOM presence alone).

### Accepted Risk: Remote Code Execution Without Cryptographic Pinning

**This is an intentional design choice.** Subresource Integrity (SRI) hash pinning would require a new Chrome Web Store release every time the trainer changes, which defeats the purpose of remote auto-updates.

| Risk | Detail |
|------|--------|
| **Supply-chain RCE** | If the GitHub Pages deployment is compromised, malicious JavaScript could execute in the page context on the user's next visit. |
| **Unversioned URL** | `trainer.min.js` is overwritten on each deploy; there is no immutable versioned path. |
| **No SRI pinning** | The extension does not verify a cryptographic hash of the downloaded script. |

**Accidental-misconfiguration guards (not compromise mitigations):** Before execution, the loader checks payload size, requires a `Content-Type` header matching JavaScript, and verifies a `__ICTrainer` substring is present. These catch empty responses, HTML error pages, and truncated downloads — but **any malicious script can include that substring**, so they do not protect against a compromised deploy.

**Monitoring guidance for operators:**
- Watch the [GitHub repository](https://github.com/hacker6284/infinite-craft-cli) and [Pages deploy workflow](https://github.com/hacker6284/infinite-craft-cli/actions) for unexpected changes to `bookmarklet/`.
- CI rebuilds `trainer.min.js` from source (`sudo/craft.sudo` via the pinned rules_sudo release + `trainer.src.mjs`, bundled by `//bookmarklet:trainer_min_js`) on every deploy — there is no committed artifact to compare against; reproducibility comes from rebuilding the Bazel target.
- Report suspicious behavior via [GitHub Issues](https://github.com/hacker6284/infinite-craft-cli/issues).

## Changes to This Policy

If we make changes to this privacy policy, we will update the "Last updated" date above.

## Contact

For questions about this privacy policy, open an issue at:
https://github.com/hacker6284/infinite-craft-cli/issues