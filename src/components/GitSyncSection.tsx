import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../hooks/api';
import { relativeTime } from './Editor/syncBadge';

export function GitSyncSection() {
  const queryClient = useQueryClient();
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: api.settings.get });
  const { data: status } = useQuery({ queryKey: ['files', 'sync', 'status'], queryFn: api.filesSync.status });

  const [remote, setRemote] = useState('');
  const [branch, setBranch] = useState('main');
  const [initError, setInitError] = useState<string | null>(null);

  // Seed inputs from saved settings once they load.
  useEffect(() => {
    if (settings) {
      setRemote(settings.gitRemoteUrl ?? '');
      setBranch(settings.gitBranch || 'main');
    }
  }, [settings?.gitRemoteUrl, settings?.gitBranch]);

  const save = useMutation({
    mutationFn: () => api.settings.updateAI({ gitRemoteUrl: remote.trim(), gitBranch: branch.trim() || 'main' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['settings'] }),
  });

  const initRepo = useMutation({
    mutationFn: api.filesSync.init,
    onMutate: () => setInitError(null),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['files', 'sync', 'status'] }),
    onError: (e: Error) => setInitError(e.message),
  });

  const dirty = remote.trim() !== (settings?.gitRemoteUrl ?? '') || (branch.trim() || 'main') !== (settings?.gitBranch || 'main');
  const remoteSaved = !!settings?.gitRemoteUrl;

  return (
    <section className="mb-8">
      <h2 className="text-lg font-medium text-[var(--color-text)] mb-4">Files Sync (Git)</h2>
      <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10 space-y-4">
        <p className="text-sm text-[var(--color-text-muted)]">
          Sync your Files folder between machines through a self-hosted git remote — your notes
          stay on your own hardware. Use an SSH URL; this machine's SSH key must be able to reach
          the host. Sync itself is the manual <span className="text-[var(--color-text)]">⟳</span> button in the Files tab.
        </p>

        <div className="space-y-1">
          <label className="text-xs text-[var(--color-text-muted)]">Remote URL (SSH)</label>
          <input
            value={remote}
            onChange={(e) => setRemote(e.target.value)}
            placeholder="git@nas:notes.git"
            className="w-full bg-[var(--color-bg)] border border-white/10 rounded px-3 py-1.5 text-sm text-[var(--color-text)] focus:outline-none focus:border-[var(--color-primary)] font-mono"
          />
        </div>

        <div className="space-y-1">
          <label className="text-xs text-[var(--color-text-muted)]">Branch</label>
          <input
            value={branch}
            onChange={(e) => setBranch(e.target.value)}
            placeholder="main"
            className="w-40 bg-[var(--color-bg)] border border-white/10 rounded px-3 py-1.5 text-sm text-[var(--color-text)] focus:outline-none focus:border-[var(--color-primary)] font-mono"
          />
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={() => save.mutate()}
            disabled={!dirty || save.isPending || !remote.trim()}
            className="px-3 py-1 text-sm bg-white/10 hover:bg-white/20 text-[var(--color-text)] rounded disabled:opacity-50 transition-colors"
          >
            {save.isPending ? 'Saving…' : 'Save'}
          </button>
          {remoteSaved && !status?.isRepo && (
            <button
              onClick={() => initRepo.mutate()}
              disabled={initRepo.isPending}
              className="px-3 py-1 text-sm bg-[var(--color-primary)]/20 text-[var(--color-primary)] hover:bg-[var(--color-primary)]/30 rounded disabled:opacity-50 transition-colors"
            >
              {initRepo.isPending ? 'Setting up…' : 'Initialize repository'}
            </button>
          )}
        </div>

        {initError && <p className="text-xs text-red-400">{initError}</p>}

        <div className="pt-3 border-t border-white/10 text-xs text-[var(--color-text-muted)] space-y-1">
          <p>Repository: {status?.isRepo ? <span className="text-green-500">ready</span> : <span>not initialized</span>}</p>
          {status?.isRepo && <p>Branch: <code className="text-[var(--color-text)]">{status.branch}</code></p>}
          {status?.remoteUrl && <p>Remote: <code className="text-[var(--color-text)]">{status.remoteUrl}</code></p>}
          <p>Last sync: {relativeTime(status?.lastSync ?? null)}</p>
        </div>
      </div>
    </section>
  );
}
