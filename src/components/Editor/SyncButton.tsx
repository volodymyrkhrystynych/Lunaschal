import { useEffect, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { syncBadge } from './syncBadge';

const TONE_CLASS: Record<string, string> = {
  idle: 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]',
  busy: 'text-yellow-400',
  pending: 'text-[var(--color-primary)] hover:opacity-80',
  error: 'text-red-400 hover:opacity-80',
};

export function SyncButton() {
  const queryClient = useQueryClient();

  const { data: status, refetch } = useQuery({
    queryKey: ['files', 'sync', 'status'],
    queryFn: api.filesSync.status,
    // Poll while a sync is in flight; otherwise stay quiet.
    refetchInterval: (q) => (q.state.data?.running ? 1500 : false),
  });

  const syncMutation = useMutation({
    mutationFn: api.filesSync.sync,
    onSuccess: () => refetch(),
  });

  // When a running sync transitions to finished, refresh the file tree so any
  // pulled changes appear. (EditorPane re-reads the open file off its own query.)
  const prevRunning = useRef(false);
  useEffect(() => {
    if (prevRunning.current && !status?.running) {
      // Refresh the tree and any open file so pulled changes show up.
      queryClient.invalidateQueries({ queryKey: ['files', 'list'] });
      queryClient.invalidateQueries({ queryKey: ['files', 'read'] });
    }
    prevRunning.current = !!status?.running;
  }, [status?.running, queryClient]);

  const badge = syncBadge(status);
  const disabled = !badge.canSync || syncMutation.isPending;
  const title = !status?.isRepo
    ? 'Set up git sync in Settings → Git Sync'
    : !status?.hasRemote
    ? 'Configure a git remote in Settings → Git Sync'
    : status?.error
    ? status.error
    : status?.conflicted
    ? 'Merge conflict — fix the markers in the affected files, then Sync again'
    : status?.remoteUrl ?? undefined;

  return (
    <button
      onClick={() => !disabled && syncMutation.mutate()}
      disabled={disabled}
      title={title}
      className={`flex items-center gap-1 text-xs px-1 transition-colors disabled:opacity-60 disabled:cursor-default ${TONE_CLASS[badge.tone]}`}
    >
      <span className={badge.tone === 'busy' ? 'animate-spin' : ''}>⟳</span>
      <span className="truncate max-w-[8rem]">{badge.label}</span>
    </button>
  );
}
