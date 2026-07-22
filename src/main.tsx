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
      // Offline: fall back to the persisted cache instead of hanging.
      networkMode: 'offlineFirst',
      // Must be >= the persister maxAge, or restored entries get GC'd on
      // hydration before they're ever shown.
      gcTime: PERSIST_MAX_AGE,
    },
    mutations: {
      // Fail-fast default: offline mutations error immediately and are NOT
      // queued. The offline-queueable writes opt into 'online' (pause + replay)
      // individually in offline/mutationDefaults.ts.
      networkMode: 'offlineFirst',
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
