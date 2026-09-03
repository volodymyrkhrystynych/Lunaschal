import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient } from '@tanstack/react-query';

vi.hoisted(() => {
  (globalThis as { indexedDB?: unknown }).indexedDB = {};
});

const idb = new Map<unknown, unknown>();
vi.mock('idb-keyval', () => ({
  createStore: () => ({}),
  get: async (k: unknown) => idb.get(k),
  set: async (k: unknown, v: unknown) => void idb.set(k, v),
  del: async (k: unknown) => void idb.delete(k),
  keys: async () => [...idb.keys()],
}));

const { storePhoto, markAttempt, getPhoto } = await import('./photoStore');
const { resumeStoredPhotos, enqueueJournalAttachment } =
  await import('./photoQueue');
const { registerOfflineMutationDefaults, MUTATION_KEYS } =
  await import('./mutationDefaults');

const jpeg = () => new File(['pixels'], 'p.jpg', { type: 'image/jpeg' });

function makeClient() {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { networkMode: 'always' },
    },
  });
  registerOfflineMutationDefaults(qc);
  return qc;
}

beforeEach(() => {
  idb.clear();
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: true, json: async () => ({ id: 'x' }) }))
  );
});

describe('resumeStoredPhotos', () => {
  it('sends a photo the persisted queue lost', async () => {
    // The gap this exists for: the tab died between writing the blob and the
    // mutation reaching the persisted cache. Nothing else would ever look at
    // that photo again.
    await storePhoto('p1', jpeg(), 'food', 'e1');
    const qc = makeClient();

    await resumeStoredPhotos(qc);

    await vi.waitFor(() =>
      expect(vi.mocked(fetch)).toHaveBeenCalledWith(
        '/api/food',
        expect.anything()
      )
    );
  });

  it('leaves a photo alone when its upload is already queued', async () => {
    await storePhoto('p1', jpeg(), 'selfie', '2026-08-22');
    const qc = makeClient();
    // Stand in for the paused mutation react-query restored from the cache.
    qc.getMutationCache()
      .build<unknown, Error, { photoId: string }, unknown>(qc, {
        mutationKey: MUTATION_KEYS.selfieUpload,
        mutationFn: () => new Promise(() => {}),
      })
      .execute({ photoId: 'p1' });

    await resumeStoredPhotos(qc);

    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it('does not retry a photo the server has already refused', async () => {
    // Terminal means terminal: a 4xx will say the same thing on every boot,
    // and the photo is kept for the user to deal with, not hammered at.
    await storePhoto('p1', jpeg(), 'food', 'e1');
    await markAttempt('p1', 'image is too large', true);
    const qc = makeClient();

    await resumeStoredPhotos(qc);

    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });
});

describe('journal attachments', () => {
  it('uploads a queued journal attachment under its own id', async () => {
    // The id is both the photoStore key and the row id the server is asked to
    // use, which is what makes the replay loop below a no-op rather than a
    // second copy of the picture.
    await storePhoto('a1', jpeg(), 'journal', 'entry-1');
    const qc = makeClient();

    await enqueueJournalAttachment(qc, 'a1', 'entry-1', 'A photo');

    const [url, init] = vi.mocked(fetch).mock.calls[0] as [
      string,
      { body: FormData },
    ];
    expect(url).toBe('/api/journal/entry-1/attachments');
    expect(init.body.get('attachmentId')).toBe('a1');
    expect(init.body.get('name')).toBe('A photo');
    // The filename survives the round trip through IndexedDB, where the File
    // became a nameless Blob — it is what the server resolves `kind` from.
    expect((init.body.get('file') as File).name).toBe('p.jpg');
  });

  it('lets go of the file only once the server has confirmed it', async () => {
    await storePhoto('a1', jpeg(), 'journal', 'entry-1');
    const qc = makeClient();

    await enqueueJournalAttachment(qc, 'a1', 'entry-1');

    expect(await getPhoto('a1')).toBeUndefined();
  });

  it('keeps the file on the device when the upload fails', async () => {
    // The whole point. Before this, a journal photo lived only in React state
    // and a failed POST was the end of it.
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: false,
        status: 500,
        json: async () => ({ error: 'nope' }),
      }))
    );
    await storePhoto('a1', jpeg(), 'journal', 'entry-1');
    const qc = makeClient();

    await enqueueJournalAttachment(qc, 'a1', 'entry-1').catch(() => {});

    const held = await getPhoto('a1');
    expect(held?.meta.attempts).toBe(1);
    expect(held?.meta.failed).toBe(false);
    expect(held?.blob).toBeDefined();
  });

  it('stops retrying a journal attachment the server refused outright', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: false,
        status: 413,
        json: async () => ({ error: 'file is too large' }),
      }))
    );
    await storePhoto('a1', jpeg(), 'journal', 'entry-1');
    const qc = makeClient();

    await enqueueJournalAttachment(qc, 'a1', 'entry-1').catch(() => {});

    // Kept, never destroyed — but not hammered at on every boot either.
    const held = await getPhoto('a1');
    expect(held?.meta.failed).toBe(true);
  });

  it('picks up a journal attachment the persisted queue lost', async () => {
    await storePhoto('a1', jpeg(), 'journal', 'entry-1');
    const qc = makeClient();

    await resumeStoredPhotos(qc);

    await vi.waitFor(() =>
      expect(vi.mocked(fetch)).toHaveBeenCalledWith(
        '/api/journal/entry-1/attachments',
        expect.anything()
      )
    );
    // Named from the filename, so a rescued orphan lands under the label the
    // composer would have given it.
    const [, init] = vi.mocked(fetch).mock.calls[0] as [
      string,
      { body: FormData },
    ];
    expect(init.body.get('name')).toBe('p');
  });

  it('leaves a journal attachment alone when its upload is already queued', async () => {
    await storePhoto('a1', jpeg(), 'journal', 'entry-1');
    const qc = makeClient();
    qc.getMutationCache()
      .build<unknown, Error, { attachmentId: string }, unknown>(qc, {
        mutationKey: MUTATION_KEYS.journalAttachment,
        mutationFn: () => new Promise(() => {}),
      })
      .execute({ attachmentId: 'a1' });

    await resumeStoredPhotos(qc);

    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it('names a nameless PDF after its own mime type, not .jpg', async () => {
    // A journal attachment need not be a picture. Calling a PDF `.jpg` would
    // make the server resolve it as an image and queue a vision caption for a
    // document.
    await storePhoto(
      'a1',
      new Blob(['%PDF'], { type: 'application/pdf' }),
      'journal',
      'entry-1'
    );

    expect((await getPhoto('a1'))?.meta.filename).toBe('a1.pdf');
  });
});

describe('a journal attachment whose entry is not there yet', () => {
  // The lane keeps the create ahead of its attachments, but a create that
  // *fails* leaves them pointing at nothing. A 404 is the one 4xx that is not
  // the file's fault, and nothing else carries a journal attachment — so it
  // must stay queueable rather than being retired.
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: false,
        status: 404,
        json: async () => ({ error: 'Not found' }),
      }))
    );
  });

  it('keeps the file queueable rather than retiring it', async () => {
    await storePhoto('a1', jpeg(), 'journal', 'entry-1');
    const qc = makeClient();

    await enqueueJournalAttachment(qc, 'a1', 'entry-1').catch(() => {});

    const held = await getPhoto('a1');
    expect(held?.meta.attempts).toBe(1);
    expect(held?.meta.failed).toBe(false);
  });

  it('so the next boot tries it again', async () => {
    await storePhoto('a1', jpeg(), 'journal', 'entry-1');
    const qc = makeClient();
    await enqueueJournalAttachment(qc, 'a1', 'entry-1').catch(() => {});

    await resumeStoredPhotos(makeClient());

    await vi.waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2));
  });
});

describe('paper is left to its own Save', () => {
  it('does not upload a held paper picture on boot', async () => {
    // Nothing in a paper reaches the server except by pressing Save — drawing
    // on a tablet with bad wifi was unusable while the app synced on its own —
    // and a boot-time upload would quietly break that. The rescue this sweep
    // exists for is not lost: the editor's Save reads the same held set for the
    // open paper and sends every picture its pages are still carrying.
    await storePhoto('img-1', jpeg(), 'paper', 'page-1', {
      x: 0,
      y: 0,
      width: 100,
      height: 100,
    });
    const qc = makeClient();

    await resumeStoredPhotos(qc);
    await new Promise(r => setTimeout(r, 20));

    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
    // And the bytes are still here, waiting for that Save.
    expect(await getPhoto('img-1')).toBeTruthy();
  });
});
