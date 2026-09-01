import type { QueryClient } from '@tanstack/react-query';
import { MUTATION_KEYS, type JournalRecordingVars } from './mutationDefaults';
import {
  finalizeRecording,
  listRecordings,
  type RecordingIdea,
  type StoredRecording,
} from './recordingStore';

/**
 * What happens to a recording once it is safely on the device.
 *
 * The recorder's job ends at "the audio is stored". This is the policy layer:
 * it decides what to try, and — the part that matters — it never lets go of the
 * audio until something confirms it landed. `recordingStore.deleteRecording` is
 * called in exactly two places, both of them after a success.
 */

const DEFAULT_NAME = 'Recording';
/** The attachment's label in the journal entry an idea capture creates. */
const IDEA_NAME = 'Idea';

/**
 * Hand a stored recording to the offline write queue, which uploads it now if
 * the backend is reachable and otherwise pauses until it is — surviving
 * reloads, because the mutation is registered under its key in
 * `registerOfflineMutationDefaults` and its variables are just the id.
 */
export function enqueueRecordingUpload(
  qc: QueryClient,
  id: string,
  name?: string,
  opts: { entryId?: string; idea?: RecordingIdea } = {}
): Promise<unknown> {
  const mutation = qc
    .getMutationCache()
    .build<unknown, Error, JournalRecordingVars, unknown>(qc, {
      mutationKey: MUTATION_KEYS.journalRecording,
    });
  return mutation.execute({
    id,
    name,
    entryId: opts.entryId,
    // Flattened out of the stored recording: these vars are structured-cloned
    // into the persisted cache and a paused upload has to be replayable from
    // them alone.
    ideaId: opts.idea?.id,
    repoId: opts.idea?.repoId,
  });
}

/** True if this recording already has a live or paused upload in flight. */
function alreadyQueued(qc: QueryClient, id: string): boolean {
  return qc
    .getMutationCache()
    .getAll()
    .some(m => {
      if (m.options.mutationKey?.[0] !== 'journal') return false;
      if (m.options.mutationKey?.[1] !== 'recording') return false;
      const vars = m.state.variables as JournalRecordingVars | undefined;
      return vars?.id === id && m.state.status === 'pending';
    });
}

/**
 * The one policy for a finished journal recording.
 *
 * Both modes upload the clip as a journal attachment. The upload passes the
 * stored recording's mode so the server can stop there (`audio`) or also
 * transcribe it into the entry body (`transcribe`). Keeping one durable path
 * means a Journal recording does not become disposable merely because it is
 * destined for text too.
 */
export async function handleFinishedRecording(
  qc: QueryClient,
  rec: StoredRecording,
  opts: { name?: string } = {}
): Promise<void> {
  await enqueueRecordingUpload(qc, rec.id, opts.name ?? DEFAULT_NAME, {
    idea: rec.idea,
  });
}

/**
 * The same policy, aimed at an entry that already exists.
 *
 * The Journal's Transcribe button records while an entry is open for editing,
 * so the clip belongs on *that* entry rather than on a new one of its own. It
 * goes through the identical durable queue — same mutation, same idempotency,
 * same "never let go of the audio until the server confirms it" — because
 * `POST /api/journal/recordings` does `INSERT OR IGNORE` on the entry: handed
 * an id that already exists, it skips the insert and attaches the file to what
 * is there.
 *
 * The transcript is not the server's job here. It was already fetched in the
 * browser and appended to the draft the user is looking at
 * (`deliverTranscript`), so the stored recording's mode is `audio` and the
 * upload carries `transcribe=false` — a second server-side pass would burn
 * another CPU transcription and write the text into the entry body underneath
 * an open editor.
 */
export async function attachRecordingToEntry(
  qc: QueryClient,
  rec: StoredRecording,
  entryId: string,
  opts: { name?: string } = {}
): Promise<void> {
  await enqueueRecordingUpload(qc, rec.id, opts.name ?? DEFAULT_NAME, {
    entryId,
  });
}

/**
 * The Ideas tab's Record button: the same durable journal upload, carrying the
 * id of the idea the clip is also being captured as.
 *
 * Deliberately not awaited by its caller. The other two are — their buttons sit
 * in a panel that shows "Saving…" — but stopping the recording *is* the save
 * here, and the idea is already on screen from the optimistic insert. Waiting
 * would park the button on "Saving…" for as long as the phone stays offline,
 * over a recording that is safely on disk.
 */
export function captureIdeaRecording(
  qc: QueryClient,
  rec: StoredRecording,
  opts: { name?: string } = {}
): Promise<unknown> {
  return enqueueRecordingUpload(qc, rec.id, opts.name ?? IDEA_NAME, {
    idea: rec.idea,
  });
}

/**
 * Pick up recordings left on the device by a previous session.
 *
 * `resumePausedMutations()` only knows about mutations React Query itself saw
 * paused. A recording that was still being written when the app was killed —
 * the screen-lock case — has no mutation at all, so it needs finding by
 * enumerating the store. Re-queueing one that is already queued is harmless:
 * the upload is idempotent server-side.
 */
export async function resumeStoredRecordings(qc: QueryClient): Promise<void> {
  const stored = await listRecordings();
  for (const rec of stored) {
    // Terminal failures wait for the user (retry or discard) rather than
    // hammering an endpoint that has already refused this file.
    if (rec.failed) continue;
    if (alreadyQueued(qc, rec.id)) continue;
    // Never finalized: the app died mid-recording. Close it and upload the
    // prefix — a truncated recording still carries most of what was said.
    if (!rec.finalized) await finalizeRecording(rec.id, { recovered: true });
    void enqueueRecordingUpload(
      qc,
      rec.id,
      rec.idea ? IDEA_NAME : DEFAULT_NAME,
      {
        idea: rec.idea,
      }
    ).catch(() => {
      // Failures are recorded on the stored recording by the mutation itself;
      // the pending strip is what surfaces them.
    });
  }
}
