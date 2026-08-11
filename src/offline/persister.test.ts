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

import { PERSIST_BUSTER, createIDBPersister } from './persister';
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

// A tripwire, not a unit test. Moving a query to useInfiniteQuery changes the
// persisted shape from a flat array to {pages, pageParams}, and a restored old
// array makes react-query read data.pages.length and throw before anything
// renders. That has now shipped three times — the journal feed (v2), the
// fanfic library (v4) and the email list (v5) — because the bump lives in a
// different file from the change that requires it.
//
// So: this list is the set of views known to use useInfiniteQuery at the
// PERSIST_BUSTER value below. Adding one fails this test, and the fix is to
// bump the buster and update both constants in the same commit.
describe('useInfiniteQuery users vs PERSIST_BUSTER', () => {
  const KNOWN_INFINITE_QUERY_FILES = [
    'src/components/Email/EmailList.tsx',
    'src/components/Fanfic/Library.tsx',
    'src/components/Journal.tsx',
    'src/components/Paper/Paper.tsx',
  ];
  const BUSTER_AT_TIME_OF_LIST = 'v5';

  it('has not gained an infinite query without a buster bump', async () => {
    const { readdirSync, readFileSync } = await import('node:fs');
    const { join, relative } = await import('node:path');

    const root = join(import.meta.dirname, '..', '..');
    const found: string[] = [];
    const walk = (dir: string) => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const full = join(dir, entry.name);
        if (entry.isDirectory()) {
          if (entry.name !== 'node_modules') walk(full);
        } else if (
          /\.tsx?$/.test(entry.name) &&
          !/\.test\.tsx?$/.test(entry.name)
        ) {
          if (readFileSync(full, 'utf8').includes('useInfiniteQuery')) {
            found.push(relative(root, full).replaceAll('\\', '/'));
          }
        }
      }
    };
    walk(join(root, 'src'));

    expect(
      found.filter(f => !f.endsWith('offline/persister.ts')).sort()
    ).toEqual(KNOWN_INFINITE_QUERY_FILES);
    expect(PERSIST_BUSTER).toBe(BUSTER_AT_TIME_OF_LIST);
  });
});
