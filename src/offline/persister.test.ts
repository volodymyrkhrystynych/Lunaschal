import { describe, it, expect, vi, beforeEach } from 'vitest';

// In-memory stand-in for IndexedDB.
const store = new Map<IDBValidKey, unknown>();
vi.mock('idb-keyval', () => ({
  get: vi.fn(async (k: IDBValidKey) => store.get(k)),
  set: vi.fn(async (k: IDBValidKey, v: unknown) => {
    store.set(k, v);
  }),
  del: vi.fn(async (k: IDBValidKey) => {
    store.delete(k);
  }),
}));

import { createIDBPersister } from './persister';
import type { PersistedClient } from '@tanstack/react-query-persist-client';

const sampleClient = {
  timestamp: 123,
  buster: 'v1',
  clientState: { mutations: [], queries: [] },
} as unknown as PersistedClient;

describe('createIDBPersister', () => {
  beforeEach(() => store.clear());

  it('round-trips persist → restore → remove', async () => {
    const persister = createIDBPersister('test-key');

    expect(await persister.restoreClient()).toBeUndefined();

    await persister.persistClient(sampleClient);
    expect(await persister.restoreClient()).toEqual(sampleClient);

    await persister.removeClient();
    expect(await persister.restoreClient()).toBeUndefined();
  });

  it('keys independently so separate persisters do not collide', async () => {
    const a = createIDBPersister('key-a');
    const b = createIDBPersister('key-b');
    await a.persistClient(sampleClient);
    expect(await b.restoreClient()).toBeUndefined();
  });
});
