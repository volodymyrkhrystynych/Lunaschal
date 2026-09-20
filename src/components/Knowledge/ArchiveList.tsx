import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api, type KnowledgeArchive } from '../../hooks/api';
import {
  groupArchivesByKind,
  healthBadge,
  isSearchable,
  sizeLabel,
} from '../../lib/knowledge';

const TONE_CLASS = {
  ok: 'text-[var(--color-text-muted)]',
  warn: 'text-amber-400',
  error: 'text-red-400',
} as const;

function ArchiveRow({ archive }: { archive: KnowledgeArchive }) {
  const client = useQueryClient();
  const toggle = useMutation({
    mutationFn: (enabled: boolean) =>
      api.knowledge.updateArchive(archive.id, { enabled }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['knowledge'] });
    },
  });
  const badge = healthBadge(archive.health);
  const searchable = isSearchable(archive);

  return (
    <div
      className={`rounded border border-white/10 p-3 bg-[var(--color-surface)] ${searchable ? '' : 'opacity-60'}`}
    >
      <div className="flex items-start gap-2">
        <div className="min-w-0">
          <div className="font-medium text-sm truncate">{archive.title}</div>
          <div className="text-xs text-[var(--color-text-muted)] mt-1">
            {[archive.date, archive.language, sizeLabel(archive.size)]
              .filter(Boolean)
              .join(' · ')}
          </div>
        </div>
        <label className="ml-auto shrink-0 flex items-center gap-1 text-[11px] text-[var(--color-text-muted)]">
          <input
            type="checkbox"
            checked={archive.enabled}
            disabled={toggle.isPending}
            onChange={event => toggle.mutate(event.target.checked)}
            aria-label={`Search ${archive.title}`}
          />
          search
        </label>
      </div>
      {badge && (
        <div
          className={`text-xs mt-1 ${TONE_CLASS[badge.tone]}`}
          title={badge.hint}
        >
          {badge.label}
        </div>
      )}
      {archive.error && (
        <div className="text-xs text-red-400 mt-1">{archive.error}</div>
      )}
    </div>
  );
}

export function ArchiveList({ archives }: { archives: KnowledgeArchive[] }) {
  const groups = groupArchivesByKind(archives);
  if (!groups.length) {
    return (
      <p className="text-sm text-[var(--color-text-muted)]">
        No .zim files found in this folder.
      </p>
    );
  }
  return (
    <div className="space-y-4">
      {groups.map(group => (
        <section key={group.kind}>
          <h3 className="text-xs uppercase tracking-wide text-[var(--color-text-muted)] mb-1">
            {group.label}
            <span className="ml-1 opacity-70">({group.archives.length})</span>
          </h3>
          <div className="space-y-2">
            {group.archives.map(archive => (
              <ArchiveRow key={archive.id} archive={archive} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
