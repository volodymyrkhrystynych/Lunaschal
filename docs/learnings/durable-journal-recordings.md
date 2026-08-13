# Journal audio that survives an iPhone

**Status: shipped**, `fix/durable-journal-recordings`.

## The bug

Two reports, one cause:

- "I hit Record, talked, and noticed it had stopped recording."
- "It was transcribing, I locked the phone, came back — the journal entry's gone."

A recording lived in exactly one place at a time. `useRecorder` called
`mr.start()` **with no timeslice**, so `ondataavailable` fired exactly once, at
stop: until the user pressed Stop, the app held nothing it could have saved.
After stop it was a `Blob` in a closure, POSTed once by a bare `fetch` with no
timeout, and on any failure the code called `setError(...)` and returned — the
blob went out of scope and was collected. Backgrounding the tab, locking the
phone, navigating away, unmounting the component, an unreachable backend, or any
4xx/5xx each destroyed the recording, and the only feedback was a truncated red
string in the bottom bar.

## The guarantee now

Audio for a journal recording is written to IndexedDB **while it is being
recorded**, and deleted only after the server confirms the entry and its
attachment exist. `deleteRecording` is called in exactly three places, all of
them after a success or an explicit user discard.

## What is not fixable, and what that implies

iOS Safari **suspends a backgrounded page**. There is no web API that keeps a
`MediaRecorder` running through a screen lock — Web Share Target, background
audio and Background Sync are all unavailable in Safari. So the design does not
try to keep recording; it makes sure everything captured up to the suspension is
already on disk, and that coming back says so:

- `mr.start(CHUNK_MS)` with `CHUNK_MS = 5000` — the change everything else
  depends on. The exposure window is five seconds, not the whole recording.
- `visibilitychange` → hidden calls `mr.requestData()`, which flushes the
  partial chunk _before_ the suspension, narrowing that window to a fraction of
  a second.
- `visibilitychange` → visible checks `mr.state`. iOS routinely leaves it
  `inactive`, and `onstop` never fires; the UI used to come back showing
  `recording` as if nothing had happened. It now finalizes what it has and says
  the recording ended while the screen was off.
- A **Screen Wake Lock** is held while recording (Safari 16.4+, feature
  detected). This is the only part that prevents the problem rather than
  recovering from it: the auto-lock is what suspends the page.
- `track.onended` (an incoming call takes the mic) and `mr.onerror` route to the
  same finalize-and-keep path. Neither was handled.

## Three decisions worth keeping

**One IndexedDB key per chunk, not one growing array.** The store is
`chunk:<id>:<seq>`. Appending to a stored `Blob[]` rewrites the whole array
every five seconds, which is quadratic in a long recording — a 40-minute walk
would rewrite hundreds of megabytes hundreds of times.

**Its own object store, not the react-query cache.** `persister.ts` structured-
clones the entire query client on every write. A blob in that graph would be
copied on every unrelated mutation in the app. For the same reason the queued
mutation's variables are `{ id }` and never the audio — which also makes a
mutation paused before a reload reconstructable from its variables alone, the
requirement `registerOfflineMutationDefaults` exists to satisfy.

**One ULID is both the entry id and the attachment id.** The phone re-POSTs a
recording on every reconnect until the server confirms it, so "the same upload
twice" is the normal path, not an edge case. `POST /api/journal/recordings`
takes both ids from the client: `INSERT OR IGNORE` for the entry (the pattern
`create_entry` already used), and an early return on an attachment id we already
hold — checked **before the file is read**, so a replay doesn't stream a hundred
megabytes to disk to discover it was already there.

## Failure policy

- Network error or 5xx → retry with backoff, keep the audio.
- Offline → the mutation _pauses_ (`networkMode: 'online'`) and replays on
  reconnect, including across reloads.
- 4xx → stop retrying, and **still keep the audio**, offered for download. The
  server refusing a file is not a reason to destroy it.
- Transcription fails → the audio is saved as an audio journal entry instead of
  being dropped. Losing the words is recoverable (the attachment has its own
  Transcribe button); losing the recording is not.

## The one thing `resumePausedMutations()` cannot do

It only replays mutations React Query itself saw paused. A recording that was
still being written when the app was killed has no mutation at all, so
`resumeStoredRecordings()` enumerates the store at boot and queues what it
finds, finalizing anything left open. That is the screen-lock case, and it is
the reason the sweep exists alongside the paused-mutation replay rather than
instead of it.

## Testing note

jsdom and node have no `indexedDB`, and `recordingStore` picks between the real
store and its in-memory fallback **once, at module load** — which happens while
static imports are still being evaluated. The stub therefore has to go in a
`vi.hoisted()` block, or the tests quietly exercise the fallback and prove
nothing about the path that has to survive a reload. `src/test/mediaRecorder.ts`
models `state`, `requestData()`, a real `mimeType` and tracks that can end on
their own, because those are exactly the paths that only fire on a phone.
