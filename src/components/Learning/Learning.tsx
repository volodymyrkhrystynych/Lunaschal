import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { useShortcuts, useShortcutScope } from '../../shortcuts/ShortcutProvider';
import { ReviewSession } from './ReviewSession';
import { Queue } from './Queue';
import { Browse } from './Browse';
import { BrainDump } from './BrainDump';
import { Folders } from './Folders';

export type LearningMode = 'review' | 'queue' | 'browse' | 'create' | 'folders';

const MODES: LearningMode[] = ['review', 'queue', 'browse', 'create', 'folders'];

export const pillClass = (active: boolean) =>
  `px-3 py-1 text-sm rounded-full border transition-colors ${active ? 'bg-[var(--color-primary)] border-[var(--color-primary)] text-white' : 'border-white/10 text-[var(--color-text-muted)] hover:text-[var(--color-text)]'}`;

const modeClass = (active: boolean, focusRing = false) =>
  `px-3 py-1 rounded ${active ? 'bg-[var(--color-primary)] text-white' : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'}${focusRing && active ? ' ring-1 ring-white/70' : ''}`;

export function Learning() {
  const [mode, setMode] = useState<LearningMode>('review');
  const [folderId, setFolderId] = useState<string | null>(null);
  const [tag, setTag] = useState<string | null>(null);
  const { level } = useShortcuts();

  const filters = { folderId: folderId ?? undefined, tag: tag ?? undefined };
  const { data: stats } = useQuery({
    queryKey: ['learning', 'stats', folderId, tag],
    queryFn: () => api.learning.getStats(filters),
  });
  const { data: tags } = useQuery({ queryKey: ['learning', 'tags'], queryFn: api.learning.getTags });
  const { data: folders } = useQuery({ queryKey: ['learning', 'folders'], queryFn: api.learning.listFolders });
  const { data: queue } = useQuery({ queryKey: ['learning', 'queue'], queryFn: api.learning.listQueue });

  // Drop stale filters when their target disappears.
  useEffect(() => {
    if (tag && tags && !tags.some((t) => t.name === tag)) setTag(null);
  }, [tags, tag]);
  useEffect(() => {
    if (folderId && folders && !folders.some((f) => f.id === folderId)) setFolderId(null);
  }, [folders, folderId]);

  const pendingCount = queue?.length ?? 0;

  useShortcutScope(1, {
    next: () => setMode((m) => MODES[Math.min(MODES.indexOf(m) + 1, MODES.length - 1)]),
    prev: () => setMode((m) => MODES[Math.max(MODES.indexOf(m) - 1, 0)]),
    create: () => setMode('create'),
  });

  return (
    <div className="flex-1 flex flex-col p-4 overflow-hidden">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-semibold text-[var(--color-text)]">Learning</h1>
        <div className="flex gap-2">
          <button onClick={() => setMode('review')} className={modeClass(mode === 'review', level === 1)}>
            Review ({stats?.due ?? 0})
          </button>
          <button onClick={() => setMode('queue')} className={modeClass(mode === 'queue', level === 1)}>
            Queue{pendingCount > 0 && (
              <span className="ml-1.5 px-1.5 py-0.5 text-xs rounded-full bg-orange-500/30 text-orange-300">{pendingCount}</span>
            )}
          </button>
          <button onClick={() => setMode('browse')} className={modeClass(mode === 'browse', level === 1)}>Browse</button>
          <button onClick={() => setMode('create')} className={modeClass(mode === 'create', level === 1)}>+ Create</button>
          <button onClick={() => setMode('folders')} className={modeClass(mode === 'folders', level === 1)}>Folders</button>
        </div>
      </div>

      {folders && folders.length > 0 && mode !== 'folders' && (
        <div className="mb-3 flex flex-wrap gap-2">
          <button onClick={() => setFolderId(null)} className={pillClass(!folderId)}>All folders</button>
          {folders.map((f) => (
            <button key={f.id} onClick={() => setFolderId(folderId === f.id ? null : f.id)}
              className={pillClass(folderId === f.id)}>
              {f.name}{f.dueCount > 0 && <span className="opacity-60 ml-1">{f.dueCount} due</span>}
            </button>
          ))}
        </div>
      )}

      {tags && tags.length > 0 && mode !== 'folders' && (
        <div className="mb-4 flex flex-wrap gap-2">
          <button onClick={() => setTag(null)} className={pillClass(!tag)}>All</button>
          {tags.map((t) => (
            <button key={t.name} onClick={() => setTag(tag === t.name ? null : t.name)}
              className={pillClass(tag === t.name)}>
              #{t.name} <span className="opacity-60">{t.count}</span>
            </button>
          ))}
        </div>
      )}

      {stats && mode !== 'folders' && (
        <div className="mb-4 grid grid-cols-5 gap-4">
          {[
            { label: 'Total Cards', value: stats.total, color: 'text-[var(--color-text)]' },
            { label: 'Due Today', value: stats.due, color: 'text-orange-400' },
            { label: 'In Queue', value: stats.pending, color: 'text-purple-400' },
            { label: 'Learning', value: stats.learning, color: 'text-blue-400' },
            { label: 'Mastered', value: stats.mastered, color: 'text-green-400' },
          ].map(({ label, value, color }) => (
            <div key={label} className="bg-[var(--color-surface)] rounded-lg border border-white/10 p-4 text-center">
              <div className={`text-2xl font-bold ${color}`}>{value}</div>
              <div className="text-sm text-[var(--color-text-muted)]">{label}</div>
            </div>
          ))}
        </div>
      )}

      <div className="flex-1 overflow-y-auto">
        {mode === 'review' && <ReviewSession folderId={folderId} tag={tag} />}
        {mode === 'queue' && <Queue />}
        {mode === 'browse' && <Browse folderId={folderId} tag={tag} onSelectTag={setTag} />}
        {mode === 'create' && <BrainDump folderId={folderId} onGenerated={() => setMode('queue')} />}
        {mode === 'folders' && <Folders />}
      </div>
    </div>
  );
}
