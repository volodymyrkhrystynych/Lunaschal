import { get, set, del } from 'idb-keyval';
import type {
  PersistedClient,
  Persister,
} from '@tanstack/react-query-persist-client';

// How long a persisted cache is considered usable. Must be <= the QueryClient's
// gcTime, or restored-but-stale entries get garbage-collected on hydration.
export const PERSIST_MAX_AGE = 1000 * 60 * 60 * 24 * 30; // 30 days

// Bump when the shape of persisted data changes in an incompatible way; a
// mismatch makes react-query-persist-client discard the old cache on restore.
export const PERSIST_BUSTER = 'v1';

const IDB_KEY = 'lunaschal-react-query';

/**
 * IndexedDB-backed persister for the whole React Query client (queries +
 * paused mutations). IndexedDB is used over localStorage because the cached
 * reads (journal, fics, chapters we've opened…) easily exceed the ~5MB Web
 * Storage cap. Shape follows the TanStack `createIDBPersister` recipe.
 */
export function createIDBPersister(idbKey: IDBValidKey = IDB_KEY): Persister {
  return {
    persistClient: async (client: PersistedClient) => {
      await set(idbKey, client);
    },
    restoreClient: async () => {
      return await get<PersistedClient>(idbKey);
    },
    removeClient: async () => {
      await del(idbKey);
    },
  };
}
