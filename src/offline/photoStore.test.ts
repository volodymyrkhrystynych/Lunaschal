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

const {
  storePhoto,
  getPhoto,
  listPhotos,
  markAttempt,
  deletePhoto,
  updatePhotoPlacement,
} = await import('./photoStore');

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

  it('stores the bytes, not the picked file itself', async () => {
    // A File from a picker is a reference to something the OS owns. On iOS
    // that reference stops resolving once the input is reset, and Safari's
    // networking process reads nothing from one cloned into IndexedDB — the
    // upload then goes out with no file part and the server answers "image is
    // required" about a photo that is sitting right there. What is stored has
    // to be a blob of our own.
    const picked = jpeg('the actual pixels', 'IMG_0042.HEIC');
    await storePhoto('p1', picked, 'selfie', '2026-08-22');

    const stored = idb.get('blob:p1') as Blob;
    expect(stored).not.toBe(picked);
    expect(stored instanceof File).toBe(false);
    expect(await stored.text()).toBe('the actual pixels');
    // The name only the File had is carried on the metadata instead.
    expect((await getPhoto('p1'))!.meta.filename).toBe('IMG_0042.HEIC');
  });

  it('refuses a photo that reads as nothing rather than queuing it', async () => {
    // An iCloud-optimized photo whose full-size original never came down. It
    // used to be stored, uploaded, and rejected by the server; failing here
    // says so while the user is still looking at the picker.
    await expect(
      storePhoto(
        'p1',
        new File([], 'IMG_0001.HEIC', { type: 'image/heic' }),
        'selfie',
        '2026-08-22'
      )
    ).rejects.toThrow(/empty/i);

    expect(idb.size).toBe(0);
    expect(await getPhoto('p1')).toBeUndefined();
  });

  it('refuses a photo the device will not read, and stores nothing', async () => {
    const unreadable = {
      type: 'image/jpeg',
      name: 'IMG_0002.jpg',
      size: 900,
      arrayBuffer: () => Promise.reject(new Error('NotReadableError')),
    } as unknown as File;

    await expect(
      storePhoto('p1', unreadable, 'selfie', '2026-08-22')
    ).rejects.toThrow(/could not read/i);

    expect(idb.size).toBe(0);
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

  it("follows a paper picture when it is moved, so a retry doesn't re-centre it", async () => {
    // What this pins: `placement` was written once, at paste time, and the
    // editor's staged edits — where a resize lived until it was uploaded — are
    // cleared by every Save whether or not the upload reached the server. So a
    // picture resized and saved on a bad connection kept the box it was pasted
    // into as its only durable record, and the next Save put it back in the
    // middle of the page.
    const pasted = { x: 449, y: 594, width: 1200, height: 1782 };
    await storePhoto('p1', jpeg('pixels'), 'paper', 'page-1', pasted);

    await updatePhotoPlacement('p1', {
      x: 80,
      y: 120,
      width: 600,
      height: 891,
    });

    const found = await getPhoto('p1');
    expect(found!.meta.placement).toEqual({
      x: 80,
      y: 120,
      width: 600,
      height: 891,
    });
    // The picture itself is untouched — this moves a box, nothing more.
    expect(await found!.blob.text()).toBe('pixels');
    expect(found!.meta.attempts).toBe(0);
  });

  it('has nothing to move for a picture that is already uploaded', async () => {
    // Save deletes the device copy the moment the server confirms it, and the
    // fold in `saveAll` runs against a list that can be a moment stale.
    await expect(
      updatePhotoPlacement('gone', { x: 1, y: 2, width: 3, height: 4 })
    ).resolves.toBeUndefined();
    expect(idb.size).toBe(0);
  });
});
