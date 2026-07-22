import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { recheckOnline } from '../offline/onlineManager';
import { useOnline } from '../offline/useOnline';

/**
 * Thin status bar shown only when the backend is unreachable, or when queued
 * offline writes are still syncing after reconnect. Everything the user does
 * while offline is saved locally and replayed automatically; this just makes
 * that state legible.
 */
export function OfflineIndicator() {
  const queryClient = useQueryClient();
  const online = useOnline();
  const [pending, setPending] = useState(0);

  useEffect(() => {
    const cache = queryClient.getMutationCache();
    const update = () =>
      setPending(cache.getAll().filter(m => m.state.isPaused).length);
    update();
    return cache.subscribe(update);
  }, [queryClient]);

  if (online && pending === 0) return null;

  const message = !online
    ? pending > 0
      ? `Offline — ${pending} change${pending === 1 ? '' : 's'} saved locally, will sync`
      : 'Offline — showing saved copy; new changes will sync when reconnected'
    : `Syncing ${pending} change${pending === 1 ? '' : 's'}…`;

  return (
    <div
      role="status"
      className="flex items-center gap-2 px-4 py-1.5 text-sm border-t"
      style={{
        background: online
          ? 'var(--color-bg-subtle)'
          : 'var(--color-warning-bg, #7a5b00)',
        color: online
          ? 'var(--color-text-muted)'
          : 'var(--color-warning-text, #fff)',
        borderColor: 'var(--color-border)',
      }}
    >
      <span
        className="inline-block w-2 h-2 rounded-full"
        style={{ background: online ? 'var(--color-primary)' : '#f0b429' }}
      />
      <span className="flex-1">{message}</span>
      {!online && (
        <button
          onClick={() => void recheckOnline()}
          className="underline underline-offset-2 hover:opacity-80"
        >
          Retry
        </button>
      )}
    </div>
  );
}
