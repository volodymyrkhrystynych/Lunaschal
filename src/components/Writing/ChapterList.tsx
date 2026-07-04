import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, type WritingChapterSummary } from '../../hooks/api';
import { useShortcuts, useShortcutScope } from '../../shortcuts/ShortcutProvider';

interface Props {
  projectId: string;
  selectedChapterId: string | null;
  onSelectChapter: (id: string) => void;
}

export function ChapterList({ projectId, selectedChapterId, onSelectChapter }: Props) {
  const [creating, setCreating] = useState(false);
  const [newTitle, setNewTitle] = useState('');
  const queryClient = useQueryClient();

  const { data: chapters, isLoading } = useQuery({
    queryKey: ['writing', 'chapters', projectId],
    queryFn: () => api.writing.listChapters(projectId),
    enabled: !!projectId,
  });

  const createChapter = useMutation({
    mutationFn: (title: string) => api.writing.createChapter(projectId, { title }),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['writing', 'chapters', projectId] });
      onSelectChapter(data.id);
      setCreating(false);
      setNewTitle('');
    },
  });

  const deleteChapter = useMutation({
    mutationFn: api.writing.deleteChapter,
    onSuccess: (_, id) => {
      queryClient.invalidateQueries({ queryKey: ['writing', 'chapters', projectId] });
      if (selectedChapterId === id) onSelectChapter('');
    },
  });

  const handleCreate = () => {
    const title = newTitle.trim();
    if (!title) return;
    createChapter.mutate(title);
  };

  const { level } = useShortcuts();

  const step = (dir: 1 | -1) => {
    if (!chapters || chapters.length === 0) return;
    const idx = chapters.findIndex((c) => c.id === selectedChapterId);
    if (idx === -1) {
      onSelectChapter(chapters[0].id);
      return;
    }
    const next = Math.min(Math.max(idx + dir, 0), chapters.length - 1);
    if (next !== idx) onSelectChapter(chapters[next].id);
  };

  useShortcutScope(2, {
    next: () => step(1),
    prev: () => step(-1),
    create: () => setCreating(true),
    drillIn: () => {
      const editor = document.querySelector<HTMLTextAreaElement>('[data-chapter-editor]');
      if (!editor) return false;
      editor.focus();
      return true;
    },
  });

  return (
    <div className="flex flex-col h-full">
      <div className="p-3 border-b border-white/10 shrink-0">
        <div className="text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider mb-2">Chapters</div>
        {creating ? (
          <div className="flex gap-1">
            <input
              autoFocus
              value={newTitle}
              onChange={e => setNewTitle(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') handleCreate(); if (e.key === 'Escape') { setCreating(false); setNewTitle(''); } }}
              placeholder="Chapter title…"
              className="flex-1 px-2 py-1 text-sm rounded bg-white/5 border border-white/20 text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)]"
            />
            <button
              onClick={handleCreate}
              disabled={createChapter.isPending}
              className="px-2 py-1 text-sm rounded bg-[var(--color-primary)]/20 text-[var(--color-primary)] hover:bg-[var(--color-primary)]/30 disabled:opacity-50"
            >
              Add
            </button>
          </div>
        ) : (
          <button
            onClick={() => setCreating(true)}
            className="w-full py-1.5 px-2 text-sm rounded border border-white/20 text-[var(--color-text)] hover:bg-white/10 transition-colors"
          >
            + New Chapter
          </button>
        )}
      </div>

      <div className="flex-1 overflow-y-auto p-2">
        {isLoading && (
          <div className="text-sm text-[var(--color-text-muted)] px-2 py-2">Loading…</div>
        )}
        {chapters?.map((chapter: WritingChapterSummary, idx: number) => (
          <div
            key={chapter.id}
            className={`group flex items-center justify-between px-2 py-2 rounded cursor-pointer transition-colors ${
              selectedChapterId === chapter.id
                ? 'bg-[var(--color-primary)]/20 text-[var(--color-primary)]'
                : 'text-[var(--color-text)] hover:bg-white/10'
            } ${level >= 2 && selectedChapterId === chapter.id ? 'ring-1 ring-[var(--color-primary)]' : ''}`}
            onClick={() => onSelectChapter(chapter.id)}
          >
            <div className="flex items-center gap-2 flex-1 min-w-0">
              <span className="text-xs text-[var(--color-text-muted)] shrink-0">{idx + 1}.</span>
              <span className="text-sm truncate">{chapter.title}</span>
            </div>
            <button
              onClick={e => { e.stopPropagation(); if (confirm(`Delete "${chapter.title}"?`)) deleteChapter.mutate(chapter.id); }}
              className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-white/20 text-[var(--color-text-muted)] hover:text-red-400 transition-all shrink-0"
              title="Delete chapter"
            >
              ✕
            </button>
          </div>
        ))}
        {!isLoading && (!chapters || chapters.length === 0) && (
          <div className="text-sm text-[var(--color-text-muted)] px-2 py-2">No chapters yet</div>
        )}
      </div>
    </div>
  );
}
