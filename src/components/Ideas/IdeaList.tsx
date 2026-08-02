import { useState } from 'react';
import { useShortcuts } from '../../shortcuts/ShortcutProvider';
import type { IdeaSummary } from '../../hooks/api';
import {
  displayTitle,
  filterIdeas,
  parseTags,
  statusClasses,
  statusLabel,
  tagCounts,
  type IdeaFilter,
} from '../../lib/ideas';
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
  const [filter, setFilter] = useState<IdeaFilter>({ status: 'all' });
  const { level } = useShortcuts();

  const tags = tagCounts(ideas);
  const visible = filterIdeas(ideas, filter);

  return (
    <>
      <IdeaCapture onCreated={onSelect} />

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
