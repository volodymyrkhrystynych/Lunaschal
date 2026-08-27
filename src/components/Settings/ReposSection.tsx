import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type Repo } from '../../hooks/api';

/**
 * Repositories the Ideas agent reads.
 *
 * A repo is registered by git URL and cloned into ./data/repos/<slug>/. Cloning
 * runs on the research worker, so this panel polls while anything is in flight
 * rather than blocking on the request — a first clone of a large repo is
 * minutes, and the graph build follows it.
 */
export function ReposSection() {
  const queryClient = useQueryClient();
  const [url, setUrl] = useState('');
  const [branch, setBranch] = useState('');
  const [error, setError] = useState('');

  const { data: repos } = useQuery({
    queryKey: ['repos'],
    queryFn: api.repos.list,
    // Only poll while something is actually moving. A settled list of repos
    // has nothing to say, and this panel sits on a page the user may leave open.
    refetchInterval: query =>
      (query.state.data ?? []).some(
        r => r.cloneState === 'pending' || r.cloneState === 'cloning'
      )
        ? 3000
        : false,
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ['repos'] });

  const add = useMutation({
    mutationFn: () =>
      api.repos.create({ remoteUrl: url.trim(), branch: branch.trim() }),
    onSuccess: () => {
      setUrl('');
      setBranch('');
      setError('');
      invalidate();
    },
    onError: (e: Error) => setError(e.message),
  });

  const pull = useMutation({
    mutationFn: api.repos.pull,
    onSuccess: invalidate,
  });
  const makeDefault = useMutation({
    mutationFn: api.repos.makeDefault,
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: api.repos.remove,
    onSuccess: invalidate,
  });

  return (
    <>
      <div>
        <h3 className="text-sm font-medium text-[var(--color-text)]">
          Repositories
        </h3>
        <p className="text-xs text-[var(--color-text-muted)] mt-1">
          Each repo is cloned into <code>data/repos/</code> and gets its own
          code wiki. The Ideas agent reads these checkouts — it never writes to
          them, and it never touches a working tree of yours.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <input
          value={url}
          onChange={e => setUrl(e.target.value)}
          placeholder="https://github.com/owner/repo.git"
          className="flex-1 min-w-56 rounded bg-[var(--color-bg)] border border-white/10 px-2 py-1 text-sm focus:outline-none focus:border-[var(--color-primary)]"
        />
        <input
          value={branch}
          onChange={e => setBranch(e.target.value)}
          placeholder="branch (optional)"
          className="w-36 rounded bg-[var(--color-bg)] border border-white/10 px-2 py-1 text-sm focus:outline-none focus:border-[var(--color-primary)]"
        />
        <button
          type="button"
          onClick={() => add.mutate()}
          disabled={!url.trim() || add.isPending}
          className="px-2 py-1 rounded text-sm bg-white/10 text-[var(--color-text)] hover:bg-white/15 disabled:opacity-50"
        >
          {add.isPending ? 'Adding…' : 'Add repo'}
        </button>
      </div>

      {error && <p className="text-xs text-red-400">{error}</p>}

      <p className="text-xs text-[var(--color-text-muted)]">
        https:// and git@host:owner/repo URLs only. SSH uses the keys you
        already have — no credentials are stored here.
      </p>

      {repos?.length === 0 && (
        <p className="text-xs text-[var(--color-text-muted)]">
          No repositories yet.
        </p>
      )}

      <ul className="space-y-2">
        {repos?.map(repo => (
          <RepoRow
            key={repo.id}
            repo={repo}
            onPull={() => pull.mutate(repo.id)}
            onMakeDefault={() => makeDefault.mutate(repo.id)}
            onRemove={() => remove.mutate(repo.id)}
          />
        ))}
      </ul>
    </>
  );
}

function RepoRow({
  repo,
  onPull,
  onMakeDefault,
  onRemove,
}: {
  repo: Repo;
  onPull: () => void;
  onMakeDefault: () => void;
  onRemove: () => void;
}) {
  const busy = repo.cloneState === 'pending' || repo.cloneState === 'cloning';

  return (
    <li className="rounded border border-white/10 bg-[var(--color-bg)] px-3 py-2">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-sm text-[var(--color-text)]">{repo.name}</span>
        {repo.isDefault && (
          <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-[var(--color-primary)]/20 text-[var(--color-primary)]">
            default
          </span>
        )}
        <span className="text-xs text-[var(--color-text-muted)]">
          {repo.remoteUrl}
        </span>
      </div>

      <div className="mt-1 text-xs text-[var(--color-text-muted)] flex flex-wrap gap-x-3">
        <span>
          {repo.cloneState === 'cloning'
            ? 'Cloning…'
            : repo.cloneState === 'pending'
              ? 'Queued'
              : repo.cloneState === 'error'
                ? 'Error'
                : `${repo.branch || 'default branch'} · ${repo.headSha?.slice(0, 7) ?? 'no sha'}`}
        </span>
        {repo.lastPulledAt && (
          <span>pulled {new Date(repo.lastPulledAt).toLocaleString()}</span>
        )}
        {/* A repo with no graph still works — the agent just loses the
            sub-second concept lookup and reads files directly. */}
        <span>
          {repo.hasGraph
            ? `graph: ${repo.graphNodeCount ?? '?'} nodes`
            : 'no graph'}
        </span>
      </div>

      {repo.cloneError && (
        <p className="mt-1 text-xs text-red-400">{repo.cloneError}</p>
      )}

      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          onClick={onPull}
          disabled={busy}
          className="px-2 py-0.5 rounded text-xs bg-white/10 text-[var(--color-text)] hover:bg-white/15 disabled:opacity-50"
        >
          {repo.hasCheckout ? 'Pull now' : 'Clone now'}
        </button>
        {!repo.isDefault && (
          <button
            type="button"
            onClick={onMakeDefault}
            className="px-2 py-0.5 rounded text-xs bg-white/10 text-[var(--color-text)] hover:bg-white/15"
          >
            Make default
          </button>
        )}
        <button
          type="button"
          onClick={onRemove}
          className="px-2 py-0.5 rounded text-xs text-red-400 hover:bg-red-400/10"
        >
          Remove
        </button>
      </div>
    </li>
  );
}
