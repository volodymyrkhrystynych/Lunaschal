import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';

/** Repositories whose merged PRs become journal entries (backend/github_prs/).
 * The server normalises a pasted URL to `owner/name` and refuses anything
 * that is not one, so the list shown is always the list it polls. */
export function PrJournalSection() {
  const queryClient = useQueryClient();
  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.settings.get,
  });
  const repos = settings?.prJournalRepos ?? [];
  const [draft, setDraft] = useState('');

  const save = useMutation({
    mutationFn: (next: string[]) =>
      api.settings.updateAI({ prJournalRepos: next }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['settings'] }),
  });

  function add() {
    const value = draft.trim();
    if (!value) return;
    save.mutate([...repos, value], { onSuccess: () => setDraft('') });
  }

  return (
    <div className="space-y-3 text-sm">
      <p className="text-xs text-[var(--color-text-muted)]">
        Each PR merged into these repositories becomes a journal entry on its
        merge day, listing how many commits landed on each day. Checked every 15
        minutes.
      </p>
      <p className="text-xs text-[var(--color-text-muted)]">
        A private repository is read with the Code tab's GitHub token
        {settings?.hasGithubToken
          ? ', which is set — its account needs read access to the repository.'
          : ', which is not set, so only public repositories will work.'}
      </p>
      {repos.length > 0 && (
        <ul className="space-y-1">
          {repos.map(repo => (
            <li
              key={repo}
              className="flex items-center gap-2 rounded border border-white/10 bg-[var(--color-bg)] px-3 py-1"
            >
              <span className="flex-1 text-[var(--color-text)]">{repo}</span>
              <button
                type="button"
                aria-label={`Remove ${repo}`}
                disabled={save.isPending}
                onClick={() => save.mutate(repos.filter(r => r !== repo))}
                className="px-2 py-0.5 rounded text-xs bg-white/10 text-[var(--color-text)] hover:bg-white/15 disabled:opacity-50"
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
      <form
        className="flex flex-wrap items-center gap-2"
        onSubmit={event => {
          event.preventDefault();
          add();
        }}
      >
        <input
          value={draft}
          onChange={event => setDraft(event.target.value)}
          placeholder="owner/name or GitHub URL"
          aria-label="Repository"
          className="flex-1 min-w-56 rounded bg-[var(--color-bg)] border border-white/10 px-2 py-1 text-sm focus:outline-none focus:border-[var(--color-primary)]"
        />
        <button
          type="submit"
          disabled={save.isPending || !draft.trim()}
          className="px-2 py-1 rounded text-sm bg-white/10 text-[var(--color-text)] hover:bg-white/15 disabled:opacity-50"
        >
          Add
        </button>
      </form>
      {save.error && (
        <p role="alert" className="text-xs text-red-400">
          {save.error.message}
        </p>
      )}
    </div>
  );
}
