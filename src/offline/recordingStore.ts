import { createStore, get, set, del, keys } from 'idb-keyval';
import { ulid } from '../lib/ulid';

/**
 * Durable on-device storage for journal audio.
 *
 * A recording used to exist in exactly one place — an un-flushed buffer inside
 * MediaRecorder, then briefly a Blob in a closure — so locking the phone,
 * backgrounding the tab or a failed upload destroyed it. This store is the
 * other place. Audio is written here *while* it is being recorded and deleted
 * only once the server has confirmed the entry and its attachment exist.
 *
 * Two deliberate choices:
 *
 * - **Its own IndexedDB store**, not the react-query cache key. The persister
 *   re-serializes the whole query client on every write (see persister.ts), and
 *   dragging a 60 MB blob through structured clone on every mutation would be
 *   pathological.
 * - **One key per chunk**, not a growing `Blob[]` under one key. Appending to a
 *   stored array means rewriting the entire array every five seconds, which is
 *   quadratic in a long recording. `chunk:<id>:<seq>` makes each append O(1).
 *
 * The id is a client-minted ULID that becomes *both* the journal entry id and
 * the attachment id, which is what lets the upload be replayed safely — see
 * `create_recording_entry` in backend/routes/journal.py.
 */

const store = idbAvailable()
  ? createStore('lunaschal-recordings', 'recordings')
  : undefined;

/** What happens to the audio once it reaches the server. */
export type RecordingMode = 'audio' | 'transcribe';

/**
 * The idea this clip is also being captured as — the Ideas tab's Record button.
 *
 * Stored beside the audio rather than held in the component that started the
 * recording, for the same reason the audio is: everything that picks a
 * recording up later (the retry button, the startup sweep after the app was
 * killed mid-recording) knows only what is in this store, and a resumed upload
 * that has forgotten its idea silently files the recording as a plain journal
 * entry instead.
 */
export interface RecordingIdea {
  id: string;
  /** Which repository to file the idea under; omitted means the default. */
  repoId?: string;
}

/**
 * The fic (and chapter) this clip is commentary on — the reader's Commentary
 * microphone.
 *
 * Stored beside the audio for the same reason `RecordingIdea` is, and with one
 * more of its own: what the commentary is *about* is decided when recording
 * starts. The reader moves on — W/S walks to the next chapter while a thought
 * is still being spoken — so a link resolved at upload time would file the
 * entry under whatever chapter happened to be open by then.
 */
export interface RecordingFic {
  ficId: string;
  /** Absent for a PDF fic, which has no chapters to link to. */
  chapterId?: string;
}

export interface StoredRecording {
  id: string;
  mode: RecordingMode;
  /** What MediaRecorder actually produced — iOS Safari gives audio/mp4. */
  mimeType: string;
  startedAt: number;
  endedAt: number | null;
  chunkCount: number;
  /** Recording is over; the audio is complete and ready to upload. */
  finalized: boolean;
  /** Ended because the OS killed the recorder, not because the user stopped. */
  recovered: boolean;
  attempts: number;
  lastError: string | null;
  /** Terminal failure (a 4xx). Stop retrying, but never throw the audio away. */
  failed: boolean;
  /** Set when the clip is an Ideas-tab capture; absent for journal recordings. */
  idea?: RecordingIdea;
  /** Set when the clip is fic commentary; absent for journal recordings. */
  fic?: RecordingFic;
}

const META_PREFIX = 'rec:';
const CHUNK_PREFIX = 'chunk:';

const metaKey = (id: string) => `${META_PREFIX}${id}`;
// Zero-padded so a plain key sort is also a chunk-order sort.
const chunkKey = (id: string, seq: number) =>
  `${CHUNK_PREFIX}${id}:${String(seq).padStart(6, '0')}`;

/** IndexedDB isn't available in every embedded webview / private mode. */
function idbAvailable(): boolean {
  try {
    return typeof indexedDB !== 'undefined' && indexedDB !== null;
  } catch {
    return false;
  }
}

// Losing a recording is the bug being fixed here, so a persistence failure must
// be loud rather than leaving the store silently empty. Same convention as the
// '[offline]' logs in persister.ts.
function warn(...args: unknown[]) {
  console.warn('[recordings]', ...args);
}

/**
 * Last-resort mirror for devices with no IndexedDB (private mode, some embedded
 * webviews). It does not survive a reload — nothing can there — but it keeps the
 * retry queue working within the session instead of collapsing back to "the
 * blob lives in one closure".
 */
const memory = new Map<string, { meta: StoredRecording; chunks: Blob[] }>();

// Every public write goes through one promise chain: chunks must land in the
// order MediaRecorder emitted them, and a read must not observe a half-applied
// append. Internal helpers below never enqueue, so this can't deadlock.
let tail: Promise<unknown> = Promise.resolve();
function enqueue<T>(fn: () => Promise<T>): Promise<T> {
  const run = tail.then(fn, fn);
  // Keep the chain alive after a rejection, but don't swallow it for the caller.
  tail = run.catch(() => undefined);
  return run;
}

async function readMeta(id: string): Promise<StoredRecording | null> {
  if (!store) return memory.get(id)?.meta ?? null;
  return (await get<StoredRecording>(metaKey(id), store)) ?? null;
}

async function writeMeta(meta: StoredRecording): Promise<void> {
  if (!store) {
    const held = memory.get(meta.id);
    memory.set(meta.id, { meta, chunks: held?.chunks ?? [] });
    return;
  }
  await set(metaKey(meta.id), meta, store);
}

/** Start a recording. Returns the record; its id is the future entry id. */
export function beginRecording(
  mode: RecordingMode,
  mimeType: string,
  opts: { idea?: RecordingIdea; fic?: RecordingFic } = {}
): Promise<StoredRecording> {
  const meta: StoredRecording = {
    id: ulid(),
    mode,
    mimeType,
    ...(opts.idea ? { idea: opts.idea } : {}),
    ...(opts.fic ? { fic: opts.fic } : {}),
    startedAt: Date.now(),
    endedAt: null,
    chunkCount: 0,
    finalized: false,
    recovered: false,
    attempts: 0,
    lastError: null,
    failed: false,
  };
  return enqueue(async () => {
    await writeMeta(meta);
    return meta;
  });
}

/**
 * Persist one timeslice. Rejects if the write fails — the caller stops the
 * recording rather than carrying on into a void, so what was already stored
 * stays usable.
 */
export function appendChunk(id: string, blob: Blob): Promise<void> {
  return enqueue(async () => {
    const meta = await readMeta(id);
    if (!meta) throw new Error(`unknown recording ${id}`);
    if (!store) {
      memory.get(id)?.chunks.push(blob);
    } else {
      await set(chunkKey(id, meta.chunkCount), blob, store);
    }
    await writeMeta({ ...meta, chunkCount: meta.chunkCount + 1 });
  });
}

/** Close a recording: the audio is complete and may now be uploaded. */
export function finalizeRecording(
  id: string,
  opts: { recovered?: boolean } = {}
): Promise<StoredRecording | null> {
  return enqueue(async () => {
    const meta = await readMeta(id);
    if (!meta) return null;
    const closed: StoredRecording = {
      ...meta,
      finalized: true,
      endedAt: meta.endedAt ?? Date.now(),
      recovered: meta.recovered || !!opts.recovered,
    };
    await writeMeta(closed);
    return closed;
  });
}

export function getRecording(id: string): Promise<StoredRecording | null> {
  return enqueue(() => readMeta(id));
}

/** Every recording still held on the device, oldest first. */
export function listRecordings(): Promise<StoredRecording[]> {
  return enqueue(async () => {
    if (!store) {
      return [...memory.values()]
        .map(v => v.meta)
        .sort((a, b) => a.startedAt - b.startedAt);
    }
    try {
      const all = await keys(store);
      const metas = await Promise.all(
        all
          .filter(k => typeof k === 'string' && k.startsWith(META_PREFIX))
          .map(k => get<StoredRecording>(k as string, store))
      );
      return metas
        .filter((m): m is StoredRecording => !!m)
        .sort((a, b) => a.startedAt - b.startedAt);
    } catch (e) {
      warn('failed to list recordings', e);
      return [];
    }
  });
}

/** The whole recording as one Blob, or null if it isn't here any more. */
export function assembleBlob(id: string): Promise<Blob | null> {
  return enqueue(async () => {
    const meta = await readMeta(id);
    if (!meta) return null;
    if (!store) {
      const chunks = memory.get(id)?.chunks ?? [];
      return new Blob(chunks, { type: meta.mimeType });
    }
    const parts: Blob[] = [];
    for (let seq = 0; seq < meta.chunkCount; seq++) {
      const part = await get<Blob>(chunkKey(id, seq), store);
      // A missing chunk means a torn write. Upload the prefix rather than
      // nothing — a truncated recording still carries most of what was said.
      if (!part) {
        warn(`recording ${id} is missing chunk ${seq}; uploading the prefix`);
        break;
      }
      parts.push(part);
    }
    return new Blob(parts, { type: meta.mimeType });
  });
}

/** Record the outcome of an upload attempt. `failed` means: stop retrying. */
export function markAttempt(
  id: string,
  error: string | null,
  failed = false
): Promise<void> {
  return enqueue(async () => {
    const meta = await readMeta(id);
    if (!meta) return;
    await writeMeta({
      ...meta,
      attempts: meta.attempts + 1,
      lastError: error,
      failed,
    });
  });
}

/**
 * Drop a recording and every chunk of it. Called on a confirmed upload, or when
 * the user explicitly discards one — never on an error path.
 */
export function deleteRecording(id: string): Promise<void> {
  return enqueue(async () => {
    const meta = await readMeta(id);
    if (!store) {
      memory.delete(id);
      return;
    }
    const count = meta?.chunkCount ?? 0;
    try {
      for (let seq = 0; seq < count; seq++) {
        await del(chunkKey(id, seq), store);
      }
      await del(metaKey(id), store);
    } catch (e) {
      warn(`failed to delete recording ${id}`, e);
    }
  });
}

/** Test seam: forget the in-memory fallback between cases. */
export function __resetMemoryFallback(): void {
  memory.clear();
}
