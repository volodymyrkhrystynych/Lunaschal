import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  QueryClient,
  defaultShouldDehydrateQuery,
} from '@tanstack/react-query';
import {
  persistQueryClientSave,
  persistQueryClientRestore,
} from '@tanstack/react-query-persist-client';

// In-memory stand-in for IndexedDB so we exercise the real persist/restore code
// paths (dehydrate → structured value → rehydrate) without a browser.
const store = new Map<unknown, unknown>();
vi.mock('idb-keyval', () => ({
  get: async (k: unknown) => store.get(k),
  set: async (k: unknown, v: unknown) => void store.set(k, v),
  del: async (k: unknown) => void store.delete(k),
}));

// Imported after the mock is registered.
const { createIDBPersister, PERSIST_BUSTER, PERSIST_MAX_AGE, QUERY_GC_TIME } =
  await import('./persister');

// The exact dehydrate rule from main.tsx.
const dehydrateOptions = {
  shouldDehydrateQuery: (
    query: Parameters<typeof defaultShouldDehydrateQuery>[0]
  ) =>
    defaultShouldDehydrateQuery(query) &&
    !(query.queryKey[0] === 'meetings' && query.queryKey[1] === 'active'),
};

function makeClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { networkMode: 'online', gcTime: QUERY_GC_TIME, retry: false },
      mutations: { networkMode: 'always' },
    },
  });
}

describe('offline persistence round-trip (real config)', () => {
  beforeEach(() => store.clear());

  it('keeps gcTime under the 32-bit setTimeout ceiling', () => {
    // Over 2^31-1 ms, setTimeout overflows and clamps to 1ms, which
    // garbage-collects every inactive query almost instantly (emptying the
    // cache). This is the bug that made offline mode appear to do nothing.
    expect(QUERY_GC_TIME).toBeLessThanOrEqual(2 ** 31 - 1);
    expect(QUERY_GC_TIME).toBeGreaterThan(1000 * 60 * 60 * 24); // > 1 day is still useful
  });

  it('persists viewed queries and restores them into a fresh client', async () => {
    const persister = createIDBPersister();
    const tasks = [{ id: '1', title: 'Buy milk', position: 0, done: false }];

    const source = makeClient();
    source.setQueryData(['tasks'], tasks);
    source.setQueryData(['todos'], []);

    await persistQueryClientSave({
      queryClient: source,
      persister,
      buster: PERSIST_BUSTER,
      dehydrateOptions,
    });
    expect(store.size).toBe(1); // something actually hit "IndexedDB"

    // Simulate a reload: brand-new client, restore from the store.
    const restored = makeClient();
    await persistQueryClientRestore({
      queryClient: restored,
      persister,
      maxAge: PERSIST_MAX_AGE,
      buster: PERSIST_BUSTER,
    });

    expect(restored.getQueryData(['tasks'])).toEqual(tasks);
    expect(restored.getQueryData(['todos'])).toEqual([]);
  });

  it('excludes the volatile meetings/active poll from the persisted cache', async () => {
    const persister = createIDBPersister();
    const source = makeClient();
    source.setQueryData(['tasks'], [{ id: '1' }]);
    source.setQueryData(['meetings', 'active'], { recording: true });

    await persistQueryClientSave({
      queryClient: source,
      persister,
      buster: PERSIST_BUSTER,
      dehydrateOptions,
    });

    const restored = makeClient();
    await persistQueryClientRestore({
      queryClient: restored,
      persister,
      maxAge: PERSIST_MAX_AGE,
      buster: PERSIST_BUSTER,
    });

    expect(restored.getQueryData(['tasks'])).toEqual([{ id: '1' }]);
    expect(restored.getQueryData(['meetings', 'active'])).toBeUndefined();
  });

  it('discards the cache when the buster changes (schema bump)', async () => {
    const persister = createIDBPersister();
    const source = makeClient();
    source.setQueryData(['tasks'], [{ id: '1' }]);
    await persistQueryClientSave({
      queryClient: source,
      persister,
      buster: PERSIST_BUSTER,
      dehydrateOptions,
    });

    const restored = makeClient();
    await persistQueryClientRestore({
      queryClient: restored,
      persister,
      maxAge: PERSIST_MAX_AGE,
      buster: 'different',
    });

    expect(restored.getQueryData(['tasks'])).toBeUndefined();
  });
});
