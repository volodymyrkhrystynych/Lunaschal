import { get, set, del } from 'idb-keyval';
import type {
  PersistedClient,
  Persister,
} from '@tanstack/react-query-persist-client';

// How long a persisted cache is considered usable. This is a Date-math age
// check at restore time, so a large value is fine here.
export const PERSIST_MAX_AGE = 1000 * 60 * 60 * 24 * 30; // 30 days

// React Query drives gcTime with setTimeout, whose delay is a 32-bit signed int:
// anything over ~24.8 days (2^31-1 ms) overflows and is clamped to 1ms, which
// silently garbage-collects every inactive query almost immediately — the whole
// persisted cache included. Keep it comfortably under that ceiling. It only
// needs to outlive navigation between views (queries go inactive when their view
// unmounts); the persisted copy, not gcTime, is what survives a reload.
export const QUERY_GC_TIME = 1000 * 60 * 60 * 24 * 20; // 20 days, < 2^31-1 ms

// Bump when the shape of persisted data changes in an incompatible way; a
// mismatch makes react-query-persist-client discard the old cache on restore.
export const PERSIST_BUSTER = 'v1';

const IDB_KEY = 'lunaschal-react-query';

// Persistence is the whole point of offline mode, and a silent failure here
// looks exactly like the feature "not working" (empty cache offline). These
// logs are cheap and let a failure be diagnosed from the browser console.
function log(...args: unknown[]) {
  console.info('[offline]', ...args);
}

/** IndexedDB isn't available in every embedded webview / private mode. */
function idbAvailable(): boolean {
  try {
    return typeof indexedDB !== 'undefined' && indexedDB !== null;
  } catch {
    return false;
  }
}

/**
 * IndexedDB-backed persister for the whole React Query client (queries +
 * paused mutations). IndexedDB is used over localStorage because the cached
 * reads (journal, fics, chapters we've opened…) easily exceed the ~5MB Web
 * Storage cap. Shape follows the TanStack `createIDBPersister` recipe, plus
 * error handling so a write failure is visible rather than silently leaving the
 * cache empty on the next offline load.
 */
export function createIDBPersister(idbKey: IDBValidKey = IDB_KEY): Persister {
  if (!idbAvailable()) {
    console.warn(
      '[offline] IndexedDB unavailable — offline persistence is disabled'
    );
  }
  return {
    persistClient: async (client: PersistedClient) => {
      try {
        await set(idbKey, client);
        log(
          `persisted ${client.clientState.queries.length} queries, ` +
            `${client.clientState.mutations.length} mutations`
        );
      } catch (e) {
        // A single non-serializable value anywhere in the cache makes the whole
        // structured-clone into IndexedDB throw, which would otherwise leave the
        // store empty forever. Surface it instead.
        console.warn('[offline] failed to persist cache', e);
      }
    },
    restoreClient: async () => {
      try {
        const client = await get<PersistedClient>(idbKey);
        log(
          client
            ? `restored ${client.clientState.queries.length} queries from cache`
            : 'no persisted cache to restore (first run or cleared)'
        );
        return client;
      } catch (e) {
        console.warn('[offline] failed to restore cache', e);
        return undefined;
      }
    },
    removeClient: async () => {
      try {
        await del(idbKey);
      } catch (e) {
        console.warn('[offline] failed to clear cache', e);
      }
    },
  };
}
