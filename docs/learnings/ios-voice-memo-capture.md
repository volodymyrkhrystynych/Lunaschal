# Getting an iOS voice memo into a journal entry: what doesn't work

**Status: unsolved.** Parked on 2026-07-30 after the attempts below. Everything
here is a record of dead ends so the next attempt doesn't re-walk them.

## The workflow being optimised

Recordings and video are taken on an iPhone/iPad in the stock **Voice Memos**
app (and Photos for video). The goal is to get one onto a journal entry without
the Files round-trip — export to Files, then open Lunaschal, then pick the file
back out — which is three deliberate steps for something that should be one.

## What was built

Journal attachments (`journal_attachments`, `backend/journal/storage.py`,
`src/components/JournalAttachments.tsx`) accept audio, video and images by file
picker, paste and drag-and-drop. The file picker path **works**. The two capture
paths meant to remove the Files step do not, on iOS.

## Attempt 1 — drag and drop

Implemented as `onDrop` on a wrapper around the whole entry editor
(`JournalAttachments` takes the title/body fields as `children` precisely so the
drop target covers all of it).

**First failure had a real cause, since fixed:** the upload route required a
non-empty filename, and a voice memo dragged out of Voice Memos is a `File` whose
`name` is the empty string. `FormData` serialized `filename=""` and the route
answered `400 file is required` — i.e. the drop had worked and the backend threw
it away. Both sides now tolerate it: `FormData.append` passes an explicit
filename synthesized from the mime type (`uploadFilenameFor`), and the route
accepts a nameless part and labels it from `_DEFAULT_NAMES`. Regression tests:
`test_upload_accepts_a_nameless_file`, `uploadFilenameFor` in
`src/lib/journalAttachments.test.ts`.

**Still reported not working after that fix, and the failure mode was never
captured.** This is the biggest open question here — see "Where to start" below.
Note the fix was correct regardless of whether it was the only problem.

## Attempt 2 — paste

Two separate obstacles, only one of which was ours:

- **Ours:** a paste carrying files we couldn't accept was silently ignored, so a
  failed paste looked identical to a dead handler. It now names what it refused
  (`rejectedFilesMessage`), and `clipboardData.items` is read as a fallback when
  `.files` comes back empty.
- **Safari's:** there is no paste affordance unless the tap lands in an editable
  field — long-pressing anywhere else offers no Paste at all. An explicit
  **Paste** button was added, calling `navigator.clipboard.read()` on the tap.

That button reports what it finds, and on iOS it does not find a copied `.m4a`.
Safari's async clipboard has historically exposed only `text/plain`, `text/html`
and `image/png`; an audio flavour appears not to survive the copy. **Assume
Ctrl+C/Ctrl+V of a voice memo is not available to a web app on iOS** unless
something has changed.

## Attempt 3 — not attempted, and why

**Web Share Target** is the API actually designed for this: the app would appear
in the Voice Memos share sheet and receive the file directly. It is
Chrome/Android only — Safari does not implement it, installed-to-home-screen or
not. This is the right answer on any other platform and simply isn't available.

## Where to start next time

1. **Get the actual drag-and-drop failure.** Nothing can be concluded until we
   know whether the drop fires at all. In order: does the drop handler run
   (does the dashed outline appear on drag-over), does a `POST
/api/journal/<id>/attachments` reach the Flask log, and what status does it
   return? The route now distinguishes its failure cases by message, so the
   response body is diagnostic.
2. **iOS Shortcuts is the most promising untried route**, and probably the real
   answer. A Shortcut can take a share-sheet input and POST it, and the app
   already has everything it needs: a REST endpoint that accepts a nameless
   multipart file, and header auth for non-localhost callers
   (`X-Lunaschal-Password`, see `backend/auth.py` — the STT listener already
   authenticates this way from another machine). This bypasses Safari's
   clipboard and drag-and-drop entirely. It needs network mode
   (`docs/external-access.md`) and probably a "most recent entry" or
   "create entry with attachment" endpoint so the Shortcut has one call to make.
3. **In-browser recording** (`MediaRecorder`) sidesteps Voice Memos altogether
   and does work in iOS Safari. It changes the workflow rather than supporting
   it — recordings would have to be started inside Lunaschal — so it is a
   different feature, not a fix for this one. Worth it only if 2 fails.
   (It exists, via the bottom bar's Record button, and as of
   `fix/durable-journal-recordings` it keeps its audio through a screen lock —
   see [durable-journal-recordings.md](durable-journal-recordings.md). It still
   does not solve _this_ problem: getting an existing Voice Memos file in.)

## What does work today

- File picker on every platform, including iOS via Files/Photo Library.
- Paste and drag-and-drop on the desktop.
- Everything downstream: nameless uploads, video as its own `kind`, opt-in
  transcription of audio _and_ video (ffmpeg reads the audio track straight out
  of the container).
