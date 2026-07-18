import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import {
  useShortcuts,
  useShortcutScope,
} from '../../shortcuts/ShortcutProvider';

export type Selection = {
  kind: 'chapter' | 'note' | 'discussion';
  id: string;
} | null;

type SectionKind = 'chapter' | 'note';

export type DocType = 'character' | 'outline' | 'worldbuilding' | 'note';

export const DOC_TYPE_LABELS: Record<DocType, string> = {
  character: 'Character',
  outline: 'Outline',
  worldbuilding: 'World',
  note: 'Note',
};

interface Props {
  projectId: string;
  selection: Selection;
  onSelect: (sel: Selection) => void;
}

export function WritingNav({ projectId, selection, onSelect }: Props) {
  const [creating, setCreating] = useState<SectionKind | null>(null);
  const [newTitle, setNewTitle] = useState('');
  const queryClient = useQueryClient();
  const { level } = useShortcuts();

  const { data: chapters } = useQuery({
    queryKey: ['writing', 'chapters', projectId],
    queryFn: () => api.writing.listChapters(projectId),
    enabled: !!projectId,
  });

  const { data: notes } = useQuery({
    queryKey: ['writing', 'notes', projectId],
    queryFn: () => api.writing.listNotes(projectId),
    enabled: !!projectId,
  });

  const { data: discussions } = useQuery({
    queryKey: ['writing', 'discussions', projectId],
    queryFn: () => api.writing.listDiscussions(projectId),
    enabled: !!projectId,
  });

  const createChapter = useMutation({
    mutationFn: (title: string) =>
      api.writing.createChapter(projectId, { title }),
    onSuccess: data => {
      queryClient.invalidateQueries({
        queryKey: ['writing', 'chapters', projectId],
      });
      onSelect({ kind: 'chapter', id: data.id });
      setCreating(null);
      setNewTitle('');
    },
  });

  const createNote = useMutation({
    mutationFn: (title: string) => api.writing.createNote(projectId, { title }),
    onSuccess: data => {
      queryClient.invalidateQueries({
        queryKey: ['writing', 'notes', projectId],
      });
      onSelect({ kind: 'note', id: data.id });
      setCreating(null);
      setNewTitle('');
    },
  });

  const createDiscussion = useMutation({
    mutationFn: () => api.writing.createDiscussion(projectId),
    onSuccess: data => {
      queryClient.invalidateQueries({
        queryKey: ['writing', 'discussions', projectId],
      });
      onSelect({ kind: 'discussion', id: data.id });
    },
  });

  const clearIfSelected = (
    kind: NonNullable<Selection>['kind'],
    id: string
  ) => {
    if (selection?.kind === kind && selection.id === id) onSelect(null);
  };

  const deleteChapter = useMutation({
    mutationFn: api.writing.deleteChapter,
    onSuccess: (_, id) => {
      queryClient.invalidateQueries({
        queryKey: ['writing', 'chapters', projectId],
      });
      clearIfSelected('chapter', id);
    },
  });

  const deleteNote = useMutation({
    mutationFn: api.writing.deleteNote,
    onSuccess: (_, id) => {
      queryClient.invalidateQueries({
        queryKey: ['writing', 'notes', projectId],
      });
      clearIfSelected('note', id);
    },
  });

  const deleteDiscussion = useMutation({
    mutationFn: api.chat.deleteConversation,
    onSuccess: (_, id) => {
      queryClient.invalidateQueries({
        queryKey: ['writing', 'discussions', projectId],
      });
      clearIfSelected('discussion', id);
    },
  });

  const handleCreate = () => {
    const title = newTitle.trim();
    if (!title || !creating) return;
    if (creating === 'chapter') createChapter.mutate(title);
    else createNote.mutate(title);
  };

  const flat: NonNullable<Selection>[] = [
    ...(chapters ?? []).map(c => ({ kind: 'chapter' as const, id: c.id })),
    ...(notes ?? []).map(n => ({ kind: 'note' as const, id: n.id })),
    ...(discussions ?? []).map(d => ({
      kind: 'discussion' as const,
      id: d.id,
    })),
  ];

  const step = (dir: 1 | -1) => {
    if (flat.length === 0) return;
    const idx = flat.findIndex(
      s => s.kind === selection?.kind && s.id === selection?.id
    );
    if (idx === -1) {
      onSelect(flat[0]);
      return;
    }
    const next = Math.min(Math.max(idx + dir, 0), flat.length - 1);
    if (next !== idx) onSelect(flat[next]);
  };

  useShortcutScope(2, {
    next: () => step(1),
    prev: () => step(-1),
    create: () => {
      if (selection?.kind === 'discussion') createDiscussion.mutate();
      else setCreating(selection?.kind === 'note' ? 'note' : 'chapter');
    },
    drillIn: () => {
      if (!selection) return false;
      const target = document.querySelector<HTMLTextAreaElement>(
        selection.kind === 'chapter'
          ? '[data-chapter-editor]'
          : selection.kind === 'note'
            ? '[data-note-editor]'
            : '[data-discussion-input]'
      );
      if (!target) return false;
      target.focus();
      return true;
    },
  });

  const isSelected = (kind: NonNullable<Selection>['kind'], id: string) =>
    selection?.kind === kind && selection.id === id;

  const rowClass = (kind: NonNullable<Selection>['kind'], id: string) =>
    `group flex items-center justify-between px-2 py-1.5 rounded cursor-pointer transition-colors ${
      isSelected(kind, id)
        ? 'bg-[var(--color-primary)]/20 text-[var(--color-primary)]'
        : 'text-[var(--color-text)] hover:bg-white/10'
    } ${level >= 2 && isSelected(kind, id) ? 'ring-1 ring-[var(--color-primary)]' : ''}`;

  const sectionHeader = (
    label: string,
    onAdd: () => void,
    addPending = false
  ) => (
    <div className="flex items-center justify-between px-2 pt-3 pb-1">
      <span className="text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">
        {label}
      </span>
      <button
        onClick={onAdd}
        disabled={addPending}
        className="text-xs px-1.5 rounded text-[var(--color-text-muted)] hover:text-[var(--color-primary)] hover:bg-white/10 transition-colors disabled:opacity-50"
        title={`New ${label.toLowerCase().replace(/s$/, '')}`}
      >
        +
      </button>
    </div>
  );

  const createInput = (placeholder: string) => (
    <div className="flex gap-1 px-2 pb-1">
      <input
        autoFocus
        value={newTitle}
        onChange={e => setNewTitle(e.target.value)}
        onKeyDown={e => {
          if (e.key === 'Enter') handleCreate();
          if (e.key === 'Escape') {
            setCreating(null);
            setNewTitle('');
          }
        }}
        placeholder={placeholder}
        className="flex-1 min-w-0 px-2 py-1 text-sm rounded bg-white/5 border border-white/20 text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)]"
      />
      <button
        onClick={handleCreate}
        disabled={createChapter.isPending || createNote.isPending}
        className="px-2 py-1 text-sm rounded bg-[var(--color-primary)]/20 text-[var(--color-primary)] hover:bg-[var(--color-primary)]/30 disabled:opacity-50"
      >
        Add
      </button>
    </div>
  );

  const deleteButton = (title: string, onDelete: () => void) => (
    <button
      onClick={e => {
        e.stopPropagation();
        if (confirm(`Delete "${title}"?`)) onDelete();
      }}
      className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-white/20 text-[var(--color-text-muted)] hover:text-red-400 transition-all shrink-0"
      title="Delete"
    >
      ✕
    </button>
  );

  return (
    <div className="flex-1 overflow-y-auto pb-2">
      {sectionHeader('Chapters', () => setCreating('chapter'))}
      {creating === 'chapter' && createInput('Chapter title…')}
      {chapters?.map((chapter, idx) => (
        <div
          key={chapter.id}
          className={rowClass('chapter', chapter.id)}
          onClick={() => onSelect({ kind: 'chapter', id: chapter.id })}
        >
          <div className="flex items-center gap-2 flex-1 min-w-0">
            <span className="text-xs text-[var(--color-text-muted)] shrink-0">
              {idx + 1}.
            </span>
            <span className="text-sm truncate">{chapter.title}</span>
          </div>
          {deleteButton(chapter.title, () => deleteChapter.mutate(chapter.id))}
        </div>
      ))}
      {chapters && chapters.length === 0 && creating !== 'chapter' && (
        <div className="text-sm text-[var(--color-text-muted)] px-2 py-1">
          No chapters yet
        </div>
      )}

      {sectionHeader('Notes', () => setCreating('note'))}
      {creating === 'note' && createInput('Note title…')}
      {notes?.map(note => (
        <div
          key={note.id}
          className={rowClass('note', note.id)}
          onClick={() => onSelect({ kind: 'note', id: note.id })}
        >
          <div className="flex items-center gap-2 flex-1 min-w-0">
            <span className="text-xs text-[var(--color-text-muted)] shrink-0">
              [{DOC_TYPE_LABELS[note.docType as DocType] ?? note.docType}]
            </span>
            <span className="text-sm truncate">{note.title}</span>
          </div>
          {deleteButton(note.title, () => deleteNote.mutate(note.id))}
        </div>
      ))}
      {notes && notes.length === 0 && creating !== 'note' && (
        <div className="text-sm text-[var(--color-text-muted)] px-2 py-1">
          No notes yet
        </div>
      )}

      {sectionHeader(
        'Discussions',
        () => createDiscussion.mutate(),
        createDiscussion.isPending
      )}
      {discussions?.map(discussion => (
        <div
          key={discussion.id}
          className={rowClass('discussion', discussion.id)}
          onClick={() => onSelect({ kind: 'discussion', id: discussion.id })}
        >
          <span className="text-sm truncate flex-1 min-w-0">
            {discussion.title || 'Untitled discussion'}
          </span>
          {deleteButton(discussion.title || 'Untitled discussion', () =>
            deleteDiscussion.mutate(discussion.id)
          )}
        </div>
      ))}
      {discussions && discussions.length === 0 && (
        <div className="text-sm text-[var(--color-text-muted)] px-2 py-1">
          No discussions yet
        </div>
      )}
    </div>
  );
}
