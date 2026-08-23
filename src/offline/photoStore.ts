import { createStore, get, set, del, keys } from 'idb-keyval';

/**
 * Durable on-device storage for photos waiting to be uploaded.
 *
 * The sibling of `recordingStore`, and separate from it on purpose. Audio is
 * captured over time — it needs chunk-by-chunk appends, a "finalized" flag and
 * a recovery path for a recorder the OS killed mid-sentence. A photo is
 * complete the instant it is picked: one blob, one write, nothing to recover.
 * Bending the recording model onto that would mean carrying four fields that
 * are always the same value.
 *
 * What both share, and what actually matters, is the contract: **the blob is
 * deleted only after the server has confirmed it.** A queued photo survives a
 * reload, a killed tab and a week offline, because losing the picture of the
 * meal is losing the meal — the text can be retyped and the photo cannot be
 * retaken.
 *
 * Ids are minted by the caller and are the *server's* ids too (food_media.id,
 * and the food entry id they hang off), which is what lets an upload be
 * replayed without producing a second copy — see `_save_media_file` in
 * backend/routes/food.py.
 */

const store = idbAvailable()
  ? createStore('lunaschal-photos', 'photos')
  : undefined;

export interface StoredPhoto {
  id: string;
  /** What this photo belongs to, so a sweep knows which upload to retry. */
  target: 'food' | 'selfie' | 'cookbook' | 'paper';
  /** The row it attaches to: a food entry, recipe or paper page id, or the
   *  selfie's date. */
  targetId: string;
  /** Where the picture sits on the page it was pasted onto, in page space.
   *  Paper only, and kept here rather than only in the mutation's variables so
   *  the boot sweep can re-place an orphan instead of guessing at a box. */
  placement?: { x: number; y: number; width: number; height: number };
  mimeType: string;
  filename: string;
  size: number;
  createdAt: number;
  attempts: number;
  lastError: string | null;
  /** Terminal failure (a 4xx). Stop retrying; never throw the photo away. */
  failed: boolean;
}

const META_PREFIX = 'photo:';
const BLOB_PREFIX = 'blob:';

const metaKey = (id: string) => `${META_PREFIX}${id}`;
const blobKey = (id: string) => `${BLOB_PREFIX}${id}`;

/** IndexedDB isn't available in every embedded webview / private mode. */
function idbAvailable(): boolean {
  try {
    return typeof indexedDB !== 'undefined' && indexedDB !== null;
  } catch {
    return false;
  }
}

function warn(...args: unknown[]) {
  console.warn('[photos]', ...args);
}

/**
 * Last-resort mirror for devices with no IndexedDB. It does not survive a
 * reload — nothing can there — but it keeps the queue working within the
 * session instead of collapsing back to "the upload must succeed right now".
 * Same fallback `recordingStore` keeps, for the same reason.
 */
const memory = new Map<string, unknown>();

async function put(key: string, value: unknown): Promise<void> {
  if (!store) {
    memory.set(key, value);
    return;
  }
  try {
    await set(key, value, store);
  } catch (e) {
    warn('write failed, keeping it in memory for this session', key, e);
    memory.set(key, value);
  }
}

async function take<T>(key: string): Promise<T | undefined> {
  if (!store) return memory.get(key) as T | undefined;
  try {
    const found = await get<T>(key, store);
    return found ?? (memory.get(key) as T | undefined);
  } catch (e) {
    warn('read failed', key, e);
    return memory.get(key) as T | undefined;
  }
}

async function drop(key: string): Promise<void> {
  memory.delete(key);
  if (!store) return;
  try {
    await del(key, store);
  } catch (e) {
    warn('delete failed', key, e);
  }
}

async function allKeys(): Promise<string[]> {
  const inMemory = [...memory.keys()];
  if (!store) return inMemory;
  try {
    const stored = (await keys(store)) as string[];
    return [...new Set([...stored, ...inMemory])];
  } catch (e) {
    warn('key listing failed', e);
    return inMemory;
  }
}

/**
 * Store one photo under a caller-minted id. Returns the metadata, so the caller
 * can queue an upload that refers to the id alone — never to the blob, which
 * must not travel through the react-query cache (the persister structured-
 * clones the whole client on every write).
 */
export async function storePhoto(
  id: string,
  file: Blob & { name?: string },
  target: StoredPhoto['target'],
  targetId: string,
  placement?: StoredPhoto['placement']
): Promise<StoredPhoto> {
  const blob = await materialize(file);
  const meta: StoredPhoto = {
    id,
    target,
    targetId,
    placement,
    mimeType: file.type || 'application/octet-stream',
    // The name is read off the original File — a materialized Blob has none —
    // and it is what tells the server a HEIC from the camera roll apart from a
    // JPEG when the mime type arrives empty.
    filename: file.name || `${id}.jpg`,
    size: blob.size,
    createdAt: Date.now(),
    attempts: 0,
    lastError: null,
    failed: false,
  };
  // Blob first: metadata pointing at a blob that was never written is a
  // queued upload that can only ever fail.
  await put(blobKey(id), blob);
  await put(metaKey(id), meta);
  return meta;
}

/**
 * Copy the picked file's bytes into a blob of our own, before anything is
 * stored or queued.
 *
 * A `File` from a picker is a *reference* to something the OS owns, not the
 * bytes: on iOS a photo-library pick points into the photo store, and the
 * reference stops resolving the moment the input that produced it is reset —
 * or, in Safari, when the blob is handed to `fetch` from a different process
 * than the one that cloned it into IndexedDB. Either way the multipart POST
 * goes out with no file part at all, and the selfie route answers the only way
 * it can: `image is required`, about a photo the device is still holding.
 *
 * Reading it here, while the reference is certainly still live, is what makes
 * a queued photo independent of the picker that produced it. It also turns the
 * two failure modes that used to surface as a server error into a local one:
 * a file that cannot be read, and an iCloud-optimized photo whose full-size
 * original never came down and reads as nothing.
 */
async function materialize(file: Blob): Promise<Blob> {
  let bytes: ArrayBuffer;
  try {
    bytes = await file.arrayBuffer();
  } catch (e) {
    warn('could not read the picked file', e);
    throw new Error(
      'Could not read that photo from this device. Try picking it again.'
    );
  }
  if (!bytes.byteLength) {
    throw new Error(
      'That photo came back empty — if it lives in iCloud, open it in Photos ' +
        'first so the full-size copy is on this device.'
    );
  }
  return new Blob([bytes], { type: file.type || 'application/octet-stream' });
}

export async function getPhoto(
  id: string
): Promise<{ meta: StoredPhoto; blob: Blob } | undefined> {
  const meta = await take<StoredPhoto>(metaKey(id));
  const blob = await take<Blob>(blobKey(id));
  if (!meta || !blob) return undefined;
  return { meta, blob };
}

/** Oldest first, so a backlog uploads in the order it was captured. */
export async function listPhotos(): Promise<StoredPhoto[]> {
  const ids = (await allKeys())
    .filter(k => k.startsWith(META_PREFIX))
    .map(k => k.slice(META_PREFIX.length));
  const metas = await Promise.all(
    ids.map(id => take<StoredPhoto>(metaKey(id)))
  );
  return metas
    .filter((m): m is StoredPhoto => !!m)
    .sort((a, b) => a.createdAt - b.createdAt);
}

/** Record an attempt without touching the photo itself. */
export async function markAttempt(
  id: string,
  error: string,
  terminal = false
): Promise<void> {
  const meta = await take<StoredPhoto>(metaKey(id));
  if (!meta) return;
  await put(metaKey(id), {
    ...meta,
    attempts: meta.attempts + 1,
    lastError: error,
    failed: meta.failed || terminal,
  });
}

/** Forget a terminal failure so the photo is queueable again.
 *
 * `failed` means "the server refused this file", which stops the boot sweep
 * retrying it forever. But the reason can stop being true — a build that
 * accepts a format it used to reject is exactly what happened to paper's HEIC
 * uploads — so a refusal has to be clearable rather than permanent. */
export async function clearFailure(id: string): Promise<void> {
  const meta = await take<StoredPhoto>(metaKey(id));
  if (!meta) return;
  await put(metaKey(id), { ...meta, failed: false, lastError: null });
}

/** Called in exactly one place: after the server has confirmed the upload. */
export async function deletePhoto(id: string): Promise<void> {
  await drop(blobKey(id));
  await drop(metaKey(id));
}
