import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import {
  QueryClient,
  defaultShouldDehydrateQuery,
} from '@tanstack/react-query';
import { PersistQueryClientProvider } from '@tanstack/react-query-persist-client';
import App from './App';
import { applyFontSize, getStoredFontSize } from './lib/fontSize';
import {
  createIDBPersister,
  PERSIST_BUSTER,
  PERSIST_MAX_AGE,
  QUERY_GC_TIME,
} from './offline/persister';
import { installBackendOnlineManager } from './offline/onlineManager';
import { registerOfflineMutationDefaults } from './offline/mutationDefaults';
import './index.css';

applyFontSize(getStoredFontSize());

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60,
      retry: 1,
      // Offline: serve the persisted cache and do NOT attempt a network fetch.
      // 'offlineFirst' would fire the request anyway and hang against an
      // unreachable backend; 'online' pauses instead, so a cached list shows
      // instantly and an uncached one resolves to empty rather than a spinner.
      networkMode: 'online',
      // Long enough to survive navigation between views (must stay under the
      // 32-bit setTimeout ceiling — see QUERY_GC_TIME). A reload is covered by
      // the persisted cache, not gcTime.
      gcTime: QUERY_GC_TIME,
    },
    mutations: {
      // Fail-fast default: offline mutations error immediately and are NOT
      // queued ('always' fires without pausing/retrying, unlike 'offlineFirst'
      // which would pause and later replay — wrong for deletes/AI). The
      // offline-queueable writes opt into 'online' (pause + replay) individually
      // in offline/mutationDefaults.ts.
      networkMode: 'always',
    },
  },
});

// "Offline" = the Flask backend is unreachable (navigator.onLine lies over
// Tailscale in network mode).
installBackendOnlineManager();
// Register the durable, replayable write-queue mutations before first render so
// resumePausedMutations() can reconstruct them after a reload.
registerOfflineMutationDefaults(queryClient);

const persister = createIDBPersister();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <PersistQueryClientProvider
      client={queryClient}
      persistOptions={{
        persister,
        maxAge: PERSIST_MAX_AGE,
        buster: PERSIST_BUSTER,
        dehydrateOptions: {
          shouldDehydrateQuery: query =>
            defaultShouldDehydrateQuery(query) &&
            // Ephemeral recording poll — never worth restoring stale.
            !(
              query.queryKey[0] === 'meetings' && query.queryKey[1] === 'active'
            ),
        },
      }}
      onSuccess={() => {
        // Cache restored — replay any writes queued while offline last session.
        void queryClient.resumePausedMutations();
      }}
    >
      <App />
    </PersistQueryClientProvider>
  </StrictMode>
);
