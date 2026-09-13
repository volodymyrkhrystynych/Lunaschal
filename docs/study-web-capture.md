# Study web capture and durable authenticated archives

**Status:** research/design note; no implementation scheduled by this document.

## Goal

Study should eventually let the user save things they can browse to, including
pages behind a login such as AWS Console, private documentation, and subscriber
sites. The saved object should remain useful when the original site changes,
requires a login, or disappears.

There are two separate problems:

1. **Capture:** obtain what the user can see in the browser session where they
   are already authenticated.
2. **Preservation:** store enough original material, metadata, and redundancy
   that the capture remains usable and verifiable later.

An LLM can help discover and repair site-specific workflows, but it should not
be the preservation layer. The original capture must be saved before an LLM
extracts, summarizes, or restructures it.

## Current Lunaschal shape

The existing Study web importer fetches a public URL on the Flask server and
stores sanitized HTML. It deliberately has no browser session, so it cannot
fetch a private page. See `backend/study/importer.py` and
`backend/study/storage.py`.

The Toronto Star/PressReader path already demonstrates a useful pattern, but it
is not LLM-driven. `backend/newspapers/pressreader.py` uses Playwright with:

- an interactive user login;
- saved browser state;
- deterministic semantic selectors;
- explicit download handling;
- validation that the downloaded PDF is the complete issue; and
- bounded subprocess lifetime and error handling.

That is a good template for a site-specific adapter. The saved session state
contains sensitive cookies/tokens, however, so the generalized capture system
must not copy that approach without encryption and an explicit decision to
retain a login session.

The shared research loop in `backend/research/agent.py` already accepts
caller-provided tools and dispatch maps. Browser tools could be added to that
loop rather than creating a second agent loop. The current research tools are
web/wiki tools, not browser-control tools.

## Recommended architecture

Use a three-layer pipeline:

```text
user-controlled browser session
        |
        v
capture original page/resources/PDF/screenshot
        |
        v
immutable archive + manifest + checksums
        |
        v
derived text/HTML/PDF/indexes/LLM notes for Study
```

The default experience should be **Save current page to Lunaschal**. The user
opens and logs into the site normally, presses Save, and the capture is sent to
Lunaschal. The backend never needs the site password or browser cookies.

For pages that expose a meaningful export, an optional site adapter can do more
than a generic capture: open a report, choose a date, click Export, download a
PDF/CSV/JSON file, and validate that the result is complete.

## Capture options

| Approach            | Authenticated pages                             | Main strengths                                                              | Main limitations                                                                                       | Recommendation                 |
| ------------------- | ----------------------------------------------- | --------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ | ------------------------------ |
| Flask fetch by URL  | No, unless credentials are copied to the server | Simple and efficient for public pages                                       | Cannot see the user's session or rendered SPA state                                                    | Keep for public pages          |
| React `iframe`      | Usually no                                      | Minimal UI work                                                             | Sites can block framing with CSP or `X-Frame-Options`; cross-origin content cannot be freely inspected | Do not use for private capture |
| Browser extension   | Yes                                             | Uses the user's existing Chrome/Edge session; works while browsing normally | Requires extension installation; mobile browser support varies                                         | Best general capture path      |
| Desktop QtWebEngine | Yes, inside its own profile                     | Native desktop browser pane/window; Lunaschal already uses QtWebEngine      | Separate login profile; desktop-only; full network capture is additional work                          | Good desktop capture option    |
| iOS `WKWebView`     | Yes, inside its own profile                     | Can run JavaScript, make PDFs, screenshots, and web archives                | Separate from Safari; less convenient for full network-level WARC capture                              | Good native iOS capture option |
| Playwright sandbox  | Yes, inside its own profile                     | Strong automation and site-specific workflows                               | Login state must be protected; browser is remote/separate; more security and maintenance               | Use for adapters and repair    |

Chrome's `activeTab` permission gives an extension temporary access to the
active tab only after an explicit user action. For high-fidelity Chromium
capture, the DevTools Protocol can record page/network state, although the
`chrome.debugger` permission is powerful and must be clearly disclosed. Useful
references are the [Chrome activeTab documentation](https://developer.chrome.com/docs/extensions/develop/concepts/activeTab),
[Chrome debugger API](https://developer.chrome.com/docs/extensions/reference/api/debugger),
and [CDP page capture](https://chromedevtools.github.io/devtools-protocol/tot/Page/).

The browser extension should initially use a narrow, explicit permission model:
user gesture, current tab, no cookie permission, and upload only to the
configured Lunaschal server. A design based on
[ArchiveWeb.page](https://webrecorder.net/archivewebpage/) is a useful reference:
it captures and replays interactive browser sessions locally and supports WARC
and WACZ export.

## LLM agent and sandbox

The LLM is practical as an **automation setup and repair agent**. It is much
less suitable as an unconstrained agent that rediscovers the workflow on every
capture.

### Setup flow

1. The user chooses **Configure importer** for a site.
2. A separate browser profile opens in a visible sandbox.
3. The user performs login and MFA. Credentials are never placed in the model
   prompt.
4. The agent observes the accessibility tree, rendered text, screenshots, and
   permitted downloads/network metadata.
5. The user states what should be saved when the goal is ambiguous.
6. The agent proposes a restricted workflow and validation rules.
7. The workflow is tested against several pages and shown to the user for
   approval.
8. The approved workflow is versioned and used deterministically at runtime.

The workflow should be a constrained recipe or DSL, not arbitrary generated
Python executed on the production machine. A recipe can contain:

- allowed host/path patterns;
- navigation and wait conditions;
- semantic clicks and form inputs;
- download or rendered-page extraction;
- maximum time, size, and navigation budgets;
- validation rules such as file type, title, date, page count, or minimum size;
- safe read-only action restrictions; and
- a recipe version and last-tested timestamp.

If the recipe later fails, the agent can run in the sandbox to diagnose the
failure and propose a new version. It should not silently mutate the production
recipe.

### Sandbox boundary

The browser process should have:

- a separate per-site profile;
- access only to a scratch/download directory and the capture endpoint;
- no access to the Lunaschal database, source tree, or unrelated credentials;
- time, memory, download-size, and navigation limits;
- a killable process boundary;
- a read-only action policy by default; and
- a durable step log that excludes secrets and signed URLs.

The agent must not be allowed to delete, purchase, submit, send, change account
settings, or otherwise perform side effects on an authenticated site without a
separate user confirmation.

For a generic page, no site-specific agent is needed: capture the rendered page,
loaded resources, text, PDF, and screenshot. Site-specific automation is for
cases such as “download the complete Toronto Star issue” or “open this AWS
report and export the JSON.”

## Durable archive format

The canonical preservation object should be WARC, preferably WARC 1.1, with
WACZ as a convenient package for transfer and replay.

- [WARC](https://www.loc.gov/preservation/digital/formats/fdd/fdd000236.shtml)
  is an open web-archiving format and the Library of Congress's preferred web
  archive format.
- [WACZ](https://specs.webrecorder.net/wacz/1.2.0/) packages WARC data with
  indexes and metadata for efficient random-access browser replay. It is a
  packaging layer, not a replacement for WARC.
- MHTML or a SingleFile-style HTML capture can be useful as a fallback or
  convenience export, but should not be the only preservation copy. SingleFile's
  own FAQ distinguishes its self-contained HTML from professional WARC-based
  archiving.

Each capture should be immutable. A second capture of the same URL creates a
new capture version rather than overwriting the old one. Store:

- original archive bytes;
- SHA-256 digest and byte count;
- capture URL and final URL;
- capture timestamp and local timezone;
- title and user-entered description;
- capture method and browser/adapter version;
- whether the page was authenticated;
- a list of included/excluded resource types;
- a manifest of derived files; and
- extraction/replay errors and validation results.

The archive is the source of truth. Extracted text, cleaned HTML, PDF,
screenshots, embeddings, and LLM-generated notes are derivatives that can be
recreated or replaced without losing the original capture.

Ingestion should stream into a temporary file, hash while writing, flush and
`fsync`, atomically rename into its immutable location, and only then commit the
database row. The client should use a stable capture ID and retry uploads
idempotently, following the durable recording/photo patterns already used in
Lunaschal.

## Authenticated-content security

The archive itself can contain private information even when it contains no
login material. AWS pages may include account IDs, billing data, internal URLs,
resource names, and customer information.

The capture pipeline should therefore:

- never archive passwords or one-time codes;
- strip `Cookie`, `Authorization`, and `Set-Cookie` headers;
- avoid POST bodies and form submissions by default;
- redact token-like query parameters where possible;
- treat the resulting capture as private data;
- protect it with the existing Lunaschal authentication boundary in network
  mode;
- replay it on an isolated archive origin with network access disabled; and
- encrypt off-device backups.

Persisting browser authentication state should be an explicit, per-site opt-in.
It should use encrypted storage or the platform credential store. Playwright
warns that saved authentication state may contain cookies and headers that can
impersonate the user; see its [authentication documentation](https://playwright.dev/docs/auth).

## Backup and fixity

Authenticated captures must be treated differently from reproducible YouTube
downloads. If a private report disappears, it may not be possible to download
it again.

The current Study design intentionally stores downloaded videos on an archive
drive outside the ordinary `data/` backup mirror. That is appropriate for
re-creatable public video, but not for private web captures. Private captures
should live in a backed-up, additive archive tier or have an explicit dedicated
backup path.

The preservation target should be:

- one working copy on the main device;
- one independent local copy on another medium;
- one off-site or otherwise geographically separate copy;
- a manifest and SHA-256 fixity check on ingest;
- periodic re-checking of every copy; and
- occasional test restores.

This follows the [NDSA Levels of Digital Preservation](https://www.ndsa.org/publications/levels-of-digital-preservation)
and [Digital Preservation Coalition guidance](https://www.dpconline.org/handbook/getting-started)
on multiple copies, geographic separation, checksums, and recovery testing.

Lunaschal's `ops/backup.sh` already creates dated database snapshots and an
additive media mirror under `data/`. The web-capture tier should be added to
that strategy deliberately, with attention to archive size and encryption, not
silently placed on the currently unbacked video archive drive.

## Native iOS direction

Making Lunaschal an iOS app makes an embedded browser feasible, but does not
automatically move Flask, SQLite, or the AI service onto the phone. The first
version should remain a client connected to the existing Lunaschal server over
HTTPS/Tailscale, with local capture staging for offline operation.

### Recommended iOS approach

Use Capacitor to package the existing React/Vite frontend and add a small Swift
plugin containing a native `WKWebView`. Capacitor is designed to be added to an
existing web project and extended with Swift plugins.

The browser screen can use a dedicated persistent `WKWebsiteDataStore`, let the
user log in, execute extraction JavaScript, create a PDF, take a snapshot, and
create a WebKit web archive. See Apple's [WKWebView documentation](https://developer.apple.com/documentation/webkit/wkwebview)
and [website data-store documentation](https://developer.apple.com/documentation/webkit/wkwebsitedatastore).

`SFSafariViewController` is not appropriate for capture because app
interactions with its webpage are intentionally not exposed. It is suitable
when the app only needs to show a website. `ASWebAuthenticationSession` is
appropriate for an OAuth-style authentication callback, not for inspecting an
arbitrary logged-in page. See Apple's [SFSafariViewController documentation](https://developer.apple.com/documentation/safariservices/sfsafariviewcontroller)
and [ASWebAuthenticationSession documentation](https://developer.apple.com/documentation/authenticationservices/aswebauthenticationsession).

On iOS, `WKWebView` is strongest for rendered HTML, text, PDF, screenshots, and
WebKit archives. Desktop Chromium plus CDP remains preferable for full
network-level WARC capture of complicated SPAs.

### Why not React Native first?

React Native is a reasonable choice for a new native mobile product, but it is
not the shortest path for this repository. Existing React DOM components,
Tailwind styling, browser APIs, canvas code, and CSS layouts would need to be
rewritten as native components. Its WebView is also a separate community
package, not a capability that eliminates the underlying WebKit/browser
constraints. See [react-native-webview](https://github.com/react-native-webview/react-native-webview).

React Native should be reconsidered only if mobile becomes the primary product
and a deliberate native UI rewrite is desired. Capacitor preserves more of the
current application while still allowing native capture functionality.

## Suggested implementation phases

### Phase 1: generic capture

- Add a browser-extension **Save current page to Lunaschal** action.
- Capture rendered page, readable text, screenshot, and PDF where possible.
- Stage the capture locally until the server confirms it.
- Add a new Study capture type rather than overloading sanitized public HTML.
- Store immutable files, metadata, hashes, and a manifest.

### Phase 2: browser-level archive

- Add Chromium CDP/network capture for WARC/WACZ.
- Add isolated replay and offline viewing.
- Keep the readable derivatives as fallback when replay cannot reproduce a
  complex application.

### Phase 3: agent-assisted adapters

- Add browser tools to the shared research loop.
- Add a visible sandbox and restricted recipe format.
- Let the agent author and test site adapters.
- Require user approval before activation.
- Run approved recipes deterministically with validation.

### Phase 4: native iOS capture

- Package the React app with Capacitor.
- Add a Swift `WKWebView` capture screen and persistent per-site profiles.
- Stage captures in the iOS app for offline upload.
- Add native background/retry behavior carefully within iOS lifecycle limits.

### Phase 5: desktop embedded browser, if needed

- Add a separate QtWebEngine browser window first.
- Use a dedicated profile rather than the main Lunaschal profile.
- Add a true Study split pane only after the separate-window workflow proves
  valuable.

## Decisions to make before implementation

1. Is the default goal saving the current page, or automating a site to obtain a
   canonical export?
2. Should saved private captures support faithful interactive replay, or is
   readable offline content plus provenance sufficient?
3. Is retaining per-site login sessions worth the security cost, or should the
   user log in when needed?
4. Will private captures be encrypted on the main device, or only in backups?
5. What archive size and backup retention policy is acceptable for WARC/WACZ
   captures?
6. Should the first capture client be a Chromium extension, a desktop
   QtWebEngine window, or the future iOS `WKWebView`?
