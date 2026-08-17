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
