import type { QueryClient } from '@tanstack/react-query';
import {
  MUTATION_KEYS,
  type FoodRecordingVars,
  type JournalRecordingVars,
} from './mutationDefaults';
import {
  finalizeRecording,
  listRecordings,
  type RecordingFic,
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
/** The same, for a clip recorded over a chapter in the fanfic reader. */
const COMMENTARY_NAME = 'Commentary';

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
  opts: { entryId?: string; idea?: RecordingIdea; fic?: RecordingFic } = {}
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
    ficId: opts.fic?.ficId,
    chapterId: opts.fic?.chapterId,
  });
}

/**
 * The food log's clip upload, which does not go through the journal at all.
 *
 * A meal is not a journal entry — it is a `food_entries` row the feed borrows —
 * so its audio is a `food_media` row rather than a journal attachment. Same
 * durability contract, different route.
 */
export function enqueueFoodRecording(
  qc: QueryClient,
  id: string,
  foodId: string,
  position?: number
): Promise<unknown> {
  const mutation = qc
    .getMutationCache()
    .build<unknown, Error, FoodRecordingVars, unknown>(qc, {
      mutationKey: MUTATION_KEYS.foodRecording,
    });
  return mutation.execute({ id, foodId, position });
}

/** True if this recording already has a live or paused upload in flight. */
function alreadyQueued(qc: QueryClient, id: string): boolean {
  return qc
    .getMutationCache()
    .getAll()
    .some(m => {
      const [group, kind] = m.options.mutationKey ?? [];
      // Both recording mutations, because the boot sweep is what would
      // otherwise queue a food clip a second time under the journal route.
      if (group !== 'journal' && group !== 'food') return false;
      if (kind !== 'recording') return false;
      const vars = m.state.variables as
        JournalRecordingVars | FoodRecordingVars | undefined;
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
    // A meal's clip goes to the food route or nowhere: filing it as a journal
    // entry would put the words in a different tab from the photograph of the
    // plate they were spoken over.
    if (rec.food) {
      void enqueueFoodRecording(qc, rec.id, rec.food.id).catch(() => undefined);
      continue;
    }
    void enqueueRecordingUpload(
      qc,
      rec.id,
      rec.idea ? IDEA_NAME : rec.fic ? COMMENTARY_NAME : DEFAULT_NAME,
      {
        idea: rec.idea,
        // Carried through here too, not just on the live path: a resumed
        // upload that has forgotten its fic files the commentary as a plain
        // journal entry with nothing saying which chapter it was about.
        fic: rec.fic,
        // And for the same reason: a composer clip resumed without its entry
        // becomes a bare entry of its own, next to the one whose words it was
        // recorded alongside. `INSERT OR IGNORE` on the route means naming an
        // entry that has not landed yet is safe — it creates it, and the
        // queued create converges on the same row.
        entryId: rec.entryId,
      }
    ).catch(() => {
      // Failures are recorded on the stored recording by the mutation itself;
      // the pending strip is what surfaces them.
    });
  }
}
