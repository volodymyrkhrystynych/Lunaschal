import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.hoisted(() => {
  // photoStore picks between IndexedDB and its in-memory fallback once, at
  // module load. Node has no indexedDB, so without this the tests would prove
  // nothing about the path that has to survive a reload.
  (globalThis as { indexedDB?: unknown }).indexedDB = {};
});

// In-memory stand-in for IndexedDB — the real store code paths run, no browser.
const idb = new Map<unknown, unknown>();
const writes: string[] = [];
vi.mock('idb-keyval', () => ({
  createStore: () => ({}),
  get: async (k: unknown) => idb.get(k),
  set: async (k: unknown, v: unknown) => {
    writes.push(String(k));
    idb.set(k, v);
  },
  del: async (k: unknown) => void idb.delete(k),
  keys: async () => [...idb.keys()],
}));

const { storePhoto, getPhoto, listPhotos, markAttempt, deletePhoto } =
  await import('./photoStore');

const jpeg = (bytes = 'jpegbytes', name = 'meal.jpg') =>
  new File([bytes], name, { type: 'image/jpeg' });

beforeEach(() => {
  idb.clear();
  writes.length = 0;
});

describe('photoStore', () => {
  it('keeps the picture, not a reference to one', async () => {
    // The blob itself has to be in IndexedDB: an object URL or a File handle
    // dies with the tab, and the photo cannot be retaken.
    await storePhoto('p1', jpeg('the actual pixels'), 'food', 'e1');

    const found = await getPhoto('p1');
    expect(await found!.blob.text()).toBe('the actual pixels');
    expect(found!.meta.target).toBe('food');
    expect(found!.meta.targetId).toBe('e1');
    expect(found!.meta.mimeType).toBe('image/jpeg');
  });

  it('writes the blob before the metadata that points at it', async () => {
    // Reversed, a crash between the two writes leaves a queued upload whose
    // photo does not exist — which fails forever instead of not existing.
    await storePhoto('p2', jpeg(), 'selfie', '2026-08-22');

    expect(writes).toEqual(['blob:p2', 'photo:p2']);
  });

  it('lists oldest first, so a backlog uploads in capture order', async () => {
    await storePhoto('p1', jpeg(), 'food', 'e1');
    await new Promise(r => setTimeout(r, 2));
    await storePhoto('p2', jpeg(), 'food', 'e2');

    expect((await listPhotos()).map(p => p.id)).toEqual(['p1', 'p2']);
  });

  it('records a failed attempt without touching the picture', async () => {
    await storePhoto('p1', jpeg('pixels'), 'food', 'e1');

    await markAttempt('p1', 'Could not reach the server');
    await markAttempt('p1', 'image is too large', true);

    const found = await getPhoto('p1');
    expect(found!.meta.attempts).toBe(2);
    expect(found!.meta.failed).toBe(true);
    expect(found!.meta.lastError).toBe('image is too large');
    // Still every byte of it.
    expect(await found!.blob.text()).toBe('pixels');
  });

  it('deletes the blob and its metadata together', async () => {
    await storePhoto('p1', jpeg(), 'food', 'e1');
    await deletePhoto('p1');

    expect(await getPhoto('p1')).toBeUndefined();
    expect(idb.size).toBe(0);
  });

  it('treats a half-deleted photo as gone rather than as an empty upload', async () => {
    await storePhoto('p1', jpeg(), 'food', 'e1');
    idb.delete('blob:p1');

    expect(await getPhoto('p1')).toBeUndefined();
  });
});
