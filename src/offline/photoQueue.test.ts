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

const { storePhoto, markAttempt } = await import('./photoStore');
const { resumeStoredPhotos } = await import('./photoQueue');
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
