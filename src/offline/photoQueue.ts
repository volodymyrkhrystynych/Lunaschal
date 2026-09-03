import type { QueryClient } from '@tanstack/react-query';
import {
  MUTATION_KEYS,
  type FoodCreateVars,
  type JournalAttachmentVars,
  type PaperImageAddVars,
  type SelfieUploadVars,
} from './mutationDefaults';
import { api, ApiError } from '../hooks/api';
import { defaultNameFor } from '../lib/journalAttachments';
import { deletePhoto, getPhoto, listPhotos, markAttempt } from './photoStore';

/**
 * Hand a stored journal attachment to the offline write queue.
 *
 * The photo twin of `enqueueRecordingUpload`, and the one path every journal
 * attachment now takes — the composer's staged files and the editor's paste
 * alike. It uploads immediately when the backend is reachable and pauses until
 * it is otherwise, sharing `JOURNAL_LANE` with the entry's own create so the
 * entry is always there first.
 *
 * The file is deleted from the device only after the server has confirmed it.
 * Before this existed the composer uploaded staged files with a bare `fetch`
 * and no retry, holding them nowhere but React state — so a photo added on a
 * bad connection was gone the moment the upload failed or the tab was reaped,
 * while the recording beside it survived in `recordingStore`. That asymmetry
 * is the bug this closes.
 */
export function enqueueJournalAttachment(
  qc: QueryClient,
  attachmentId: string,
  entryId: string,
  name?: string
): Promise<unknown> {
  return qc
    .getMutationCache()
    .build<unknown, Error, JournalAttachmentVars, unknown>(qc, {
      mutationKey: MUTATION_KEYS.journalAttachment,
    })
    .execute({ attachmentId, entryId, name });
}

/**
 * The boot-time sweep for photos the device is still holding.
 *
 * Nearly always there is nothing to do: a queued upload is a *paused mutation*,
 * react-query persists those, and `resumePausedMutations()` replays them after
 * a reload without any help from here. This exists for the gap that leaves —
 * a photo written to the store in the instant before the tab was killed, so the
 * mutation carrying its id never made it into the persisted cache, or a cache
 * dropped by a `PERSIST_BUSTER` bump between releases.
 *
 * Without it those photos would sit in IndexedDB forever: stored, safe, and
 * never uploaded by anything. The sweep is what makes "the blob is deleted only
 * after the server confirms it" a promise rather than a leak.
 */
export async function resumeStoredPhotos(qc: QueryClient): Promise<void> {
  const stored = await listPhotos();
  for (const photo of stored) {
    // Terminal failures wait for the user rather than hammering an endpoint
    // that has already refused this file.
    if (photo.failed) continue;
    if (alreadyQueued(qc, photo.id)) continue;

    if (photo.target === 'paper') {
      // Paper is the one target this sweep deliberately leaves alone. Nothing
      // in a paper reaches the server except by pressing Save — drawing on a
      // tablet with bad wifi was unusable while the app synced on its own — and
      // a boot-time upload would quietly break that. The rescue path is not
      // lost: the editor's Save reads this same `listPhotos()` set for the open
      // paper and sends every picture its pages are still holding, so an orphan
      // is picked up the next time that paper is saved.
      continue;
    }

    if (photo.target === 'journal') {
      // The label is re-derived from the filename rather than stored beside
      // it: it is what the composer passed in the first place, so a rescued
      // orphan lands under the name it would have had.
      void enqueueJournalAttachment(
        qc,
        photo.id,
        photo.targetId,
        defaultNameFor(photo.filename)
      );
      continue;
    }

    if (photo.target === 'cookbook') {
      // A recipe's photo cannot be re-sent as a create — the title and the text
      // live in the mutation's variables, not on the photo — so an orphan is
      // attached to the recipe it belongs to instead. The recipe's own create
      // is a separate queued mutation, persisted on its own.
      void attachRecipePhoto(photo.id, photo.targetId);
      continue;
    }

    const run =
      photo.target === 'selfie'
        ? qc
            .getMutationCache()
            .build<unknown, Error, SelfieUploadVars, unknown>(qc, {
              mutationKey: MUTATION_KEYS.selfieUpload,
            })
            .execute({ photoId: photo.id, date: photo.targetId })
        : qc
            .getMutationCache()
            .build<unknown, Error, FoodCreateVars, unknown>(qc, {
              mutationKey: MUTATION_KEYS.foodCreate,
            })
            // The meal's own row is idempotent by id, so re-sending the create
            // with the photo attached is the safe way to land an orphan: if the
            // entry is already there, only the photo is new.
            .execute({ id: photo.targetId, photoIds: [photo.id] });

    // Failures are recorded on the stored photo by the mutation itself.
    void run.catch(() => {});
  }
}

async function attachRecipePhoto(
  photoId: string,
  recipeId: string
): Promise<void> {
  const stored = await getPhoto(photoId);
  if (!stored) return;
  try {
    await api.cookbook.addMedia(recipeId, [stored.blob], [photoId]);
    await deletePhoto(photoId);
  } catch (e) {
    // A 404 means the recipe itself has not landed yet — its own queued create
    // will carry this photo when it does, so this is not a failure of the
    // photo. Everything else is recorded the same way the mutations do it.
    const message = e instanceof Error ? e.message : 'Upload failed';
    const gone = e instanceof ApiError && e.status === 404;
    if (!gone) {
      await markAttempt(
        photoId,
        message,
        e instanceof ApiError && e.status >= 400 && e.status < 500
      );
    }
  }
}

function alreadyQueued(qc: QueryClient, photoId: string): boolean {
  return qc
    .getMutationCache()
    .getAll()
    .some(m => {
      if (m.state.status !== 'pending') return false;
      const key = m.options.mutationKey;
      if (key?.[0] === 'lifestyle' && key?.[1] === 'selfie') {
        return (
          (m.state.variables as SelfieUploadVars | undefined)?.photoId ===
          photoId
        );
      }
      if (key?.[0] === 'paper' && key?.[1] === 'image') {
        return (
          (m.state.variables as PaperImageAddVars | undefined)?.imageId ===
          photoId
        );
      }
      if (key?.[0] === 'journal' && key?.[1] === 'attachment') {
        return (
          (m.state.variables as JournalAttachmentVars | undefined)
            ?.attachmentId === photoId
        );
      }
      if (key?.[0] === 'cookbook' && key?.[1] === 'create') {
        return (
          (
            m.state.variables as { photoIds?: string[] } | undefined
          )?.photoIds?.includes(photoId) ?? false
        );
      }
      if (key?.[0] === 'food' && key?.[1] === 'create') {
        return (
          (m.state.variables as FoodCreateVars | undefined)?.photoIds?.includes(
            photoId
          ) ?? false
        );
      }
      return false;
    });
}
