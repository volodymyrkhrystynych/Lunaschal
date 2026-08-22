import { createStore, get, set, del, keys } from 'idb-keyval';

/**
 * The page saves a device is still holding.
 *
 * The third on-device store, and the one with the least in common with the
 * other two: a paper page save is **last-write-wins**. Its endpoint is a PUT of
 * the whole page, so ten queued saves of the same page are nine saves of
 * something nobody will ever see. This store therefore keeps *one record per
 * page*, overwritten each time, and the queued mutation reads it at the moment
 * it actually runs — so a page written on all afternoon with no signal uploads
 * once, carrying the afternoon's final state.
 *
 * That is also why the mutation's variables are a page id and nothing else.
 *
 * Paper matters more than the rest of the offline story: it is the one feature
 * whose contents exist *only* on the tablet they were written on. The canvas
 * already buffers ink to IndexedDB as it is drawn (see PaperCanvas), so the
 * strokes themselves were never at risk — what was missing is the part that
 * gets them to the server without the user remembering to press Save again
 * once the wifi is back.
 */

const store = idbAvailable()
  ? createStore('lunaschal-pages', 'pages')
  : undefined;

export interface PendingPageSave {
  pageId: string;
  strokes: string;
  width: number;
  height: number;
  /** The canvas revision this payload was taken at, so the editor can clear
   *  its dirty flag for exactly the ink that was uploaded and no more. */
  revision: number;
  updatedAt: number;
}

const META_PREFIX = 'page:';
const SNAPSHOT_PREFIX = 'snap:';

const metaKey = (id: string) => `${META_PREFIX}${id}`;
const snapshotKey = (id: string) => `${SNAPSHOT_PREFIX}${id}`;

function idbAvailable(): boolean {
  try {
    return typeof indexedDB !== 'undefined' && indexedDB !== null;
  } catch {
    return false;
  }
}

function warn(...args: unknown[]) {
  console.warn('[pages]', ...args);
}

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

/** Replace whatever this page had pending. Newer is simply better here. */
export async function storePageSave(
  pageId: string,
  data: { strokes: string; width: number; height: number; revision: number },
  snapshot: Blob
): Promise<PendingPageSave> {
  const meta: PendingPageSave = { pageId, ...data, updatedAt: Date.now() };
  await put(snapshotKey(pageId), snapshot);
  await put(metaKey(pageId), meta);
  return meta;
}

export async function getPageSave(
  pageId: string
): Promise<{ meta: PendingPageSave; snapshot: Blob } | undefined> {
  const meta = await take<PendingPageSave>(metaKey(pageId));
  const snapshot = await take<Blob>(snapshotKey(pageId));
  if (!meta || !snapshot) return undefined;
  return { meta, snapshot };
}

export async function listPageSaves(): Promise<PendingPageSave[]> {
  const inMemory = [...memory.keys()];
  let all = inMemory;
  if (store) {
    try {
      all = [...new Set([...((await keys(store)) as string[]), ...inMemory])];
    } catch (e) {
      warn('key listing failed', e);
    }
  }
  const ids = all
    .filter(k => k.startsWith(META_PREFIX))
    .map(k => k.slice(META_PREFIX.length));
  const metas = await Promise.all(
    ids.map(id => take<PendingPageSave>(metaKey(id)))
  );
  return metas
    .filter((m): m is PendingPageSave => !!m)
    .sort((a, b) => a.updatedAt - b.updatedAt);
}

/**
 * Drop the pending save, but only up to the revision that was uploaded. Ink
 * drawn while the upload was in flight leaves a *newer* record behind, and
 * deleting that would throw away strokes the server has never seen.
 *
 * Keyed on the canvas revision rather than a timestamp on purpose: two saves
 * can land in the same millisecond, and "same millisecond" would then read as
 * "same save".
 */
export async function clearPageSave(
  pageId: string,
  uploadedRevision: number
): Promise<void> {
  const meta = await take<PendingPageSave>(metaKey(pageId));
  if (meta && meta.revision > uploadedRevision) return;
  await drop(snapshotKey(pageId));
  await drop(metaKey(pageId));
}
