# Lunaschal Apply — the browser extension

The last mile of the Jobs feature. The backend can discover postings, score
them and tailor a resume, but it cannot fill in a Greenhouse form behind your
logged-in session. This can.

Desktop only: Chrome on Android and iOS have no extensions, which is why the
phone's job is triage (the feed's Queue/Dismiss) and the desktop's is applying.

## Installing

No build step — the source _is_ the extension.

1. `chrome://extensions` → enable **Developer mode**
2. **Load unpacked** → select this `extension/` directory
3. If Lunaschal is not on `http://localhost:5000`, open the extension's
   **Settings** and set the address (the dev server is `:5001`)

There is no icon set, so Chrome shows the default puzzle piece. Pin it.

## Using it

Open a posting you have queued in Lunaschal. The extension matches the tab's
URL against your applications; if it cannot (or several match), pick one from
the popup — that choice sticks for the tab, which is what makes multi-page
Workday forms work.

- **Toolbar → Fill this form** — reads every labelled field, answers them all
  in one call, and shows what came from where.
- **Right-click a field → Answer this with Lunaschal** — just that one.
- **Attach my resume** — downloads the tailored PDF and puts it in the file
  input, named `Your Name Resume.pdf`.
- **Dictate** — records in the popup, transcribes through Lunaschal's own
  local Whisper/Parakeet, and appends to the last field you touched.

Answers are recorded against the application as they are filled, so they are
still there a year later when someone asks what you told them. Nothing is ever
submitted for you — the extension fills, you press the button.

## How it is put together

| file                      | role                                                         |
| ------------------------- | ------------------------------------------------------------ |
| `background.js`           | service worker: the only thing that talks to Lunaschal       |
| `content.js`              | injected on demand; reads and fills the page                 |
| `lib/fields.js`           | pure: label derivation, field classification. Vitest-covered |
| `lib/filename.js`         | pure: mirrors `backend/jobs/render.py`'s sanitizer           |
| `popup.js` / `options.js` | extension pages                                              |

Three constraints shaped all of it:

**Content scripts cannot call the backend.** They have been subject to CORS
since Chrome 85 and Flask sends no CORS headers. A service-worker fetch covered
by `host_permissions` is exempt, so every request goes through
`chrome.runtime.sendMessage`.

**There is no host permission for job sites.** `content.js` is injected with
`chrome.scripting.executeScript` after a click, which `activeTab` covers. That
is also why it works on a Greenhouse board embedded in a company's own domain —
no match-pattern list could enumerate those.

**Setting `.value` does not work on React forms**, and nearly every modern ATS
is React. React ignores an `input` event whose value matches what it last
wrote, so fills go through the prototype's native setter. See `setNativeValue`
in `content.js` before changing anything there.

## Tests

`lib/` is plain ES modules with no `chrome.*` in them, so Vitest imports them
directly — `vite.config.ts`'s `include` covers `extension/**/*.test.js`.

```bash
npx vitest run extension/
```

Label derivation is where the risk is (a wrong label means the model answers a
question nobody asked), so the fixtures there are the real shapes: Greenhouse's
`label[for]`, Lever's wrapping label, Ashby's `aria-label`, and Workday's bare
`<div>` above the input.

What no test covers is injection into a live ATS page. That one is manual.

## FF.net browser downloads

The same Chrome/Chromium extension can fetch FF.net stories and account lists
through a dedicated tab in your ordinary browser session. It reads rendered
pages; it does not export cookies, solve challenges, or disguise automation.
Lunaschal continues to own parsing, saved chapters, the queue, and pacing.

1. Load this directory unpacked as above. If already installed, **Reload** it
   in `chrome://extensions` to load the new scripts and permission.
2. In extension Settings, set your Lunaschal server address (and password for
   a remote server). Use the same address as the app whose queue you want.
3. Open the extension popup → **FF.net downloads** → **Connect browser**.
   Grant the requested access to `https://www.fanfiction.net/*`. Connect selects
   browser mode in Lunaschal, but does not clear an existing manual/challenge
   pause; use **Resume paused downloads** if needed.
4. Start or resume a story/collection import in Lunaschal. FF.net collection
   scans in browser mode do not require copied cookies. Sign in within the
   download tab when prompted, using the account whose favorites/follows you
   want to import.
5. Keep the **control tab and download tab open**. You can use other tabs.
   If a challenge appears, open the FF.net tab, complete it manually, then
   choose **Continue after verification / sign-in** in the control tab.
   If sign-in redirected you elsewhere, use **Retry page after interval**.

Pause / Resume and the editable interval remain in the Library. The interval
paces top-level navigations requested by Lunaschal; the browser controls its
redirects and subresources. A Retry respects the existing deadline and any
server cooldown. Browser protection may still ask for verification later.

**Disconnect never switches back to HTTP.** Saved chapters, pending page
requests, and scan checkpoints stay on the server. After a browser restart,
extension reload, or closed control tab, reopen FF.net downloads and Connect.
If the old connection disappeared without Disconnect, its ownership expires
after three minutes. A missing/closed download tab is reported; Retry recreates
it at the next allowed time. Only one control tab may drive the queue at once.
Use the Library's download-method selector to explicitly return to Direct HTTP.

The new `webRequest` permission observes response status/Retry-After only for
the assigned download tab's FF.net main document. FF.net host access is optional
and requested on Connect. No site-page content script can call the browser
queue handlers; those messages are accepted only from `ffn.html`.

Implementation: `ffn.js` is the control UI, `lib/ffn.js` is the tested navigation
and capture loop, and `background.js` keeps the shared server/auth path. Tests
cover navigation, manual challenges, wrong pages, disconnects, cooldowns and
durable replies. A Chromium smoke test was also run with a fake backend; live
FF.net challenge acceptance has not been verified.
