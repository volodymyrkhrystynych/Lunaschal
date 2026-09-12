import { useCallback, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { ulid } from '../lib/ulid';
import { useRecorder, type RecorderStatus } from './useRecorder';
import {
  deleteRecording,
  type StoredRecording,
} from '../offline/recordingStore';
import {
  enqueueFoodRecording,
  enqueueRecordingUpload,
} from '../offline/recordingQueue';

/**
 * What a composer's clips are about. The shape decides which ids get minted
 * and where `commit` sends them.
 */
export type ClipTarget =
  /**
   * `entryId` when the entry already exists (the feed's edit mode); omitted in
   * the composer, where the id is minted at the first chunk instead.
   */
  | { kind: 'journal'; entryId?: string }
  | { kind: 'idea'; repoId?: string }
  | { kind: 'fic'; ficId: string; chapterId?: string }
  | { kind: 'food' };

/**
 * What a clip's journal attachment is called, matching the names the old
 * capture paths used. `food` is here only to keep the map exhaustive — a meal
 * clip is `food_media`, which carries no name.
 */
const NAMES: Record<ClipTarget['kind'], string> = {
  journal: 'Recording',
  idea: 'Idea',
  fic: 'Commentary',
  food: 'Meal',
};

interface ClipIds {
  /** The journal entry every clip in this session lands on. Unused for food. */
  entryId: string;
  /** The idea, for an Ideas capture. */
  ideaId?: string;
  /** The meal, for a food capture. */
  foodId?: string;
}

export interface ClipStage {
  /** Staged clips, in record order. */
  clips: StoredRecording[];
  status: RecorderStatus;
  recording: boolean;
  /**
   * The gap between the tap and the microphone actually being live —
   * `getUserMedia`'s prompt plus the first store write, reported by useRecorder.
   */
  starting: boolean;
  /** True while the recorder is closing a clip out — briefly, on stop. */
  busy: boolean;
  error: string;
  notice: string;
  /** Start or stop; stopping stages the clip and does nothing else. */
  toggle: () => void;
  /** Throw a staged clip away, audio included. */
  remove: (id: string) => void;
  /**
   * The id the composer's own row is created under — the journal entry, the
   * idea, or the meal, depending on the target. Mints one if nothing has been
   * recorded, so a typed-only submit still has an id to use.
   */
  claimId: () => string;
  /**
   * Hand every staged clip to the durable upload queue. Not to be awaited by
   * the UI: an offline composer's clips pause here until the backend is back,
   * and the entry itself is already saved.
   */
  commit: () => Promise<void>;
  /** Forget the staged clips without touching the audio (after a commit). */
  reset: () => void;
}

/**
 * A composer's microphone: record, stop, record again, then send.
 *
 * The one rule this exists to enforce is that **stopping a clip is not a
 * save**. It writes the audio to `recordingStore` and puts a chip on screen;
 * no transcription runs, nothing is uploaded, and the composer's Save button
 * is never disabled by any of it. Committing is what sends — the typed text,
 * the photos and the clips together, in one deliberate action.
 *
 * That is a change from what the Ideas and fanfic capture buttons used to do,
 * where stopping *was* the save. One clip per thought was the cost: there was
 * no way to pause, take a breath and add a second one, and no way to type a
 * line alongside what you had just said.
 *
 * **The ids are minted at the first chunk, not at Send.** `resumeStoredRecordings`
 * sweeps the device at boot and uploads whatever it finds, so a clip that has
 * forgotten which meal or idea it belongs to is filed as a bare journal entry
 * — the audio survives, detached from everything that gave it meaning. Minting
 * early means a composer killed mid-thought still lands its clips in the right
 * place; Send is then only the part that adds the words and the pictures.
 */
export function useClipStage(target: ClipTarget): ClipStage {
  const qc = useQueryClient();
  const [clips, setClips] = useState<StoredRecording[]>([]);
  const [notice, setNotice] = useState('');
  const idsRef = useRef<ClipIds | null>(null);
  // Read inside `toggle` and `commit`, which are recreated on every render;
  // holding it in a ref keeps a target change (the reader walking to the next
  // chapter) from being captured stale by an in-flight recording.
  const targetRef = useRef(target);
  targetRef.current = target;

  const ids = useCallback((): ClipIds => {
    const t = targetRef.current;
    // An entry that already exists is never cached: edit mode drives one
    // recorder for the whole feed, so the id has to be whichever entry is open
    // now rather than whichever was open the first time Record was pressed.
    if (t.kind === 'journal' && t.entryId) return { entryId: t.entryId };
    if (idsRef.current) return idsRef.current;
    const next: ClipIds = { entryId: ulid() };
    if (t.kind === 'idea') next.ideaId = ulid();
    if (t.kind === 'food') next.foodId = next.entryId;
    idsRef.current = next;
    return next;
  }, []);

  const recorder = useRecorder(
    // No transcript comes back to the browser: the server transcribes the clip
    // after it is uploaded, which is what stops the composer waiting on it.
    () => undefined,
    undefined,
    {
      durable: true,
      onNotice: setNotice,
      onRecording: rec => {
        setClips(current => [...current, rec]);
      },
    }
  );
  const starting = recorder.starting ?? false;

  const toggle = useCallback(() => {
    if (recorder.status === 'recording') {
      recorder.stop();
      return;
    }
    if (recorder.status !== 'idle' || starting) return;
    setNotice('');
    const t = targetRef.current;
    const claimed = ids();
    void recorder.start('transcribe', {
      durable: true,
      entryId: t.kind === 'food' ? undefined : claimed.entryId,
      idea:
        t.kind === 'idea'
          ? { id: claimed.ideaId!, ...(t.repoId ? { repoId: t.repoId } : {}) }
          : undefined,
      fic:
        t.kind === 'fic'
          ? {
              ficId: t.ficId,
              ...(t.chapterId ? { chapterId: t.chapterId } : {}),
            }
          : undefined,
      food: t.kind === 'food' ? { id: claimed.foodId! } : undefined,
    });
  }, [ids, recorder, starting]);

  const remove = useCallback((id: string) => {
    setClips(current => current.filter(c => c.id !== id));
    // An explicit user discard — one of the few places the audio may be
    // destroyed without the server having confirmed it.
    void deleteRecording(id).catch(() => undefined);
  }, []);

  const claimId = useCallback((): string => {
    const claimed = ids();
    const t = targetRef.current;
    if (t.kind === 'idea') return claimed.ideaId!;
    if (t.kind === 'food') return claimed.foodId!;
    return claimed.entryId;
  }, [ids]);

  const commit = useCallback(async () => {
    const claimed = ids();
    const name = NAMES[targetRef.current.kind];
    // Awaited one at a time, deliberately. The transcripts are appended to the
    // entry in the order the uploads land, and a parallel `Promise.all` would
    // make that order the network's decision — two clips of one thought coming
    // back the wrong way round. Nobody awaits this function, so a clip that
    // pauses offline holds up only the clips behind it, which are on disk.
    for (const [i, clip] of clips.entries()) {
      // Read off the clip, not off the current target. What a clip is about was
      // decided when it started recording, and the target moves underneath a
      // long-lived stage: the fanfic reader walks to the next chapter with W/S
      // while a thought is still being spoken, and sending the chapter that is
      // open at Save files the commentary under the wrong one. This is the same
      // reason `resumeStoredRecordings` reads them back off the store.
      try {
        if (clip.food || claimed.foodId) {
          await enqueueFoodRecording(
            qc,
            clip.id,
            clip.food?.id ?? claimed.foodId!,
            i
          );
        } else {
          await enqueueRecordingUpload(qc, clip.id, name, {
            entryId: clip.entryId ?? claimed.entryId,
            ...(clip.idea ? { idea: clip.idea } : {}),
            ...(clip.fic ? { fic: clip.fic } : {}),
          });
        }
      } catch {
        // The mutation records the failure on the stored recording and keeps
        // the audio; the pending strip is what surfaces it. Carrying on means
        // one bad clip does not strand the ones after it.
      }
    }
  }, [clips, ids, qc]);

  const reset = useCallback(() => {
    setClips([]);
    idsRef.current = null;
    setNotice('');
  }, []);

  return {
    clips,
    status: recorder.status,
    recording: recorder.status === 'recording',
    starting,
    busy:
      starting ||
      recorder.status === 'saving' ||
      recorder.status === 'transcribing',
    error: recorder.error,
    notice,
    toggle,
    remove,
    claimId,
    commit,
    reset,
  };
}
