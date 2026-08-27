import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useShortcuts } from '../../shortcuts/ShortcutProvider';
import { api, type IdeaSummary } from '../../hooks/api';
import {
  displayTitle,
  filterIdeas,
  implementationClasses,
  implementationLabel,
  parseTags,
  resolveImplementation,
  statusClasses,
  statusLabel,
  tagCounts,
  type IdeaFilter,
} from '../../lib/ideas';
import {
  ALL_REPOS,
  getStoredIdeaRepo,
  resolveIdeaRepo,
  setStoredIdeaRepo,
} from '../../lib/ideaRepoPersistence';
import { IdeaCapture } from './IdeaCapture';

interface IdeaListProps {
  ideas: IdeaSummary[];
  isLoading: boolean;
  selectedId: string;
  onSelect: (id: string) => void;
}

export function IdeaList({
  ideas,
  isLoading,
  selectedId,
  onSelect,
}: IdeaListProps) {
  // Read once, on mount: the stored id cannot be validated yet because the repo
  // list has not loaded, so the effect below re-resolves it when it arrives.
  const [filter, setFilter] = useState<IdeaFilter>(() => ({
    status: 'all',
    repoId: getStoredIdeaRepo() ?? ALL_REPOS,
  }));
  const { level } = useShortcuts();

  const { data: repos } = useQuery({
    queryKey: ['repos'],
    queryFn: api.repos.list,
  });

  // One-shot, the CollapsibleSection autoExpand pattern: validate the stored
  // selection against what actually exists, then never touch it again. Running
  // on every repos refetch would yank the filter out from under someone who had
  // just changed it.
  const resolved = useRef(false);
  useEffect(() => {
    if (!repos || resolved.current) return;
    resolved.current = true;
    const next = resolveIdeaRepo(
      getStoredIdeaRepo(),
      repos.map(r => r.id)
    );
    setFilter(f => (f.repoId === next ? f : { ...f, repoId: next }));
  }, [repos]);

  const selectRepo = (repoId: string) => {
    setStoredIdeaRepo(repoId === ALL_REPOS ? null : repoId);
    setFilter(f => ({ ...f, repoId }));
  };

  const tags = tagCounts(ideas);
  const visible = filterIdeas(ideas, filter);

  // The switcher only earns its row when there is a choice to make. With one
  // repo — or none — it is a control that can only be set one way.
  const showRepoSwitcher = (repos?.length ?? 0) > 1;
  const captureRepoId =
    filter.repoId && filter.repoId !== ALL_REPOS ? filter.repoId : undefined;

  return (
    <>
      <IdeaCapture onCreated={onSelect} repoId={captureRepoId} />

      {showRepoSwitcher && (
        <div className="px-3 py-2 border-b border-white/10 shrink-0">
          <select
            value={filter.repoId ?? ALL_REPOS}
            onChange={e => selectRepo(e.target.value)}
            aria-label="Repository"
            className="w-full rounded bg-[var(--color-bg)] border border-white/10 px-2 py-1 text-sm focus:outline-none focus:border-[var(--color-primary)]"
          >
            <option value={ALL_REPOS}>All repositories</option>
            {repos?.map(repo => (
              <option key={repo.id} value={repo.id}>
                {repo.name}
                {repo.cloneState === 'ready' ? '' : ' (not ready)'}
              </option>
            ))}
          </select>
        </div>
      )}

      {tags.length > 0 && (
        <div className="flex flex-wrap gap-1 px-3 py-2 border-b border-white/10 shrink-0">
          {tags.map(tag => (
            <button
              key={tag.name}
              type="button"
              onClick={() =>
                setFilter(f => ({
                  ...f,
                  tag: f.tag === tag.name ? undefined : tag.name,
                }))
              }
              className={`px-1.5 py-0.5 rounded text-xs ${
                filter.tag === tag.name
                  ? 'bg-[var(--color-primary)]/20 text-[var(--color-primary)]'
                  : 'bg-white/5 text-[var(--color-text-muted)] hover:bg-white/10'
              }`}
            >
              #{tag.name} {tag.count}
            </button>
          ))}
        </div>
      )}

      <div className="flex-1 overflow-y-auto">
        {isLoading ? (
          <p className="p-3 text-sm text-[var(--color-text-muted)]">
            Loading ideas…
          </p>
        ) : visible.length === 0 ? (
          <p className="p-3 text-sm text-[var(--color-text-muted)]">
            {ideas.length === 0
              ? 'No ideas yet.'
              : 'Nothing matches that filter.'}
          </p>
        ) : (
          <ul>
            {visible.map(idea => {
              const selected = idea.id === selectedId;
              const impl = resolveImplementation(idea);
              return (
                <li key={idea.id}>
                  <button
                    type="button"
                    onClick={() => onSelect(idea.id)}
                    aria-current={selected ? 'true' : undefined}
                    className={`w-full text-left px-3 py-2 border-b border-white/5 ${
                      selected
                        ? 'bg-[var(--color-primary)]/20 text-[var(--color-primary)]'
                        : 'text-[var(--color-text)] hover:bg-white/10'
                    } ${level === 1 && selected ? 'ring-1 ring-[var(--color-primary)]' : ''}`}
                  >
                    <span className="block text-sm truncate">
                      {displayTitle(idea)}
                    </span>
                    <span className="flex items-center gap-1.5 mt-1 text-xs">
                      <span
                        className={`px-1.5 py-0.5 rounded ${statusClasses(idea.status)}`}
                      >
                        {statusLabel(idea.status)}
                      </span>
                      {impl.verdict && (
                        <span
                          className={`px-1.5 py-0.5 rounded ${implementationClasses(impl)}`}
                        >
                          {implementationLabel(impl)}
                        </span>
                      )}
                      {idea.openQuestionCount > 0 && (
                        <span
                          className="text-amber-300"
                          title={`${idea.openQuestionCount} decision${idea.openQuestionCount === 1 ? '' : 's'} needed`}
                        >
                          ? {idea.openQuestionCount}
                        </span>
                      )}
                      {idea.hasPlan && (
                        <span
                          className="text-[var(--color-text-muted)]"
                          title="Has a plan"
                        >
                          📄
                        </span>
                      )}
                      {idea.articleCount > 0 && (
                        <span
                          className="text-[var(--color-text-muted)]"
                          title={`${idea.articleCount} research note${idea.articleCount === 1 ? '' : 's'}`}
                        >
                          📚 {idea.articleCount}
                        </span>
                      )}
                      {idea.sketchCount > 0 && (
                        <span
                          className="text-[var(--color-text-muted)]"
                          title={`${idea.sketchCount} sketch${idea.sketchCount === 1 ? '' : 'es'}`}
                        >
                          🖊 {idea.sketchCount}
                        </span>
                      )}
                      {parseTags(idea.tags).map(tag => (
                        <span
                          key={tag}
                          className="text-[var(--color-text-muted)]"
                        >
                          #{tag}
                        </span>
                      ))}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </>
  );
}
