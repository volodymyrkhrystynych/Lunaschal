import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { useWritingNoteUpdate } from '../../offline/mutationDefaults';
import { DOC_TYPE_LABELS, type DocType } from './WritingNav';

interface Props {
  noteId: string;
}

const SAVE_DEBOUNCE_MS = 1500;

export function NoteEditor({ noteId }: Props) {
  const [content, setContent] = useState('');
  const [title, setTitle] = useState('');
  const [editingTitle, setEditingTitle] = useState(false);
  const [saveStatus, setSaveStatus] = useState<'saved' | 'saving' | 'unsaved'>(
    'saved'
  );
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const loadedNoteRef = useRef<string | null>(null);

  const { data: note, isLoading } = useQuery({
    queryKey: ['writing', 'note', noteId],
    queryFn: () => api.writing.getNote(noteId),
    enabled: !!noteId,
  });

  // Offline-queueable: idempotent last-write-wins PATCH; the registered
  // defaults invalidate the writing caches on reconnect.
  const updateNote = useWritingNoteUpdate({
    onSuccess: () => setSaveStatus('saved'),
  });

  useEffect(() => {
    if (note && loadedNoteRef.current !== noteId) {
      setContent(note.content);
      setTitle(note.title);
      setSaveStatus('saved');
      loadedNoteRef.current = noteId;
    }
  }, [note, noteId]);

  useEffect(() => {
    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    };
  }, []);

  const handleContentChange = (value: string) => {
    setContent(value);
    setSaveStatus('unsaved');
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      setSaveStatus('saving');
      updateNote.mutate({ noteId, content: value });
    }, SAVE_DEBOUNCE_MS);
  };

  const handleTitleSave = () => {
    const trimmed = title.trim();
    if (!trimmed) return;
    setEditingTitle(false);
    updateNote.mutate({ noteId, title: trimmed });
  };

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)]">
        Loading…
      </div>
    );
  }

  const statusLabel =
    saveStatus === 'saved'
      ? 'Saved'
      : saveStatus === 'saving'
        ? 'Saving…'
        : 'Unsaved';
  const statusColor =
    saveStatus === 'saved'
      ? 'text-green-500'
      : saveStatus === 'saving'
        ? 'text-yellow-400'
        : 'text-[var(--color-text-muted)]';

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2 border-b border-white/10 bg-[var(--color-surface)] shrink-0 gap-3">
        {editingTitle ? (
          <input
            autoFocus
            value={title}
            onChange={e => setTitle(e.target.value)}
            onBlur={handleTitleSave}
            onKeyDown={e => {
              if (e.key === 'Enter') handleTitleSave();
              if (e.key === 'Escape') {
                setTitle(note?.title ?? '');
                setEditingTitle(false);
              }
            }}
            className="flex-1 bg-transparent text-sm font-medium text-[var(--color-text)] border-b border-[var(--color-primary)] focus:outline-none"
          />
        ) : (
          <button
            onClick={() => setEditingTitle(true)}
            className="flex-1 text-left text-sm font-medium text-[var(--color-text)] hover:text-[var(--color-primary)] transition-colors truncate"
            title="Click to rename"
          >
            {note?.title}
          </button>
        )}
        <div className="flex items-center gap-3 shrink-0 text-xs text-[var(--color-text-muted)]">
          <select
            value={note?.docType ?? 'note'}
            onChange={e =>
              updateNote.mutate({ noteId, docType: e.target.value })
            }
            aria-label="Note type"
            className="px-2 py-1 rounded bg-white/5 border border-white/20 text-[var(--color-text)] focus:outline-none focus:border-[var(--color-primary)]"
          >
            {(Object.entries(DOC_TYPE_LABELS) as [DocType, string][]).map(
              ([val, label]) => (
                <option key={val} value={val}>
                  {label}
                </option>
              )
            )}
          </select>
          <span className={statusColor}>{statusLabel}</span>
        </div>
      </div>

      <textarea
        data-note-editor
        value={content}
        onChange={e => handleContentChange(e.target.value)}
        placeholder="Write your note…"
        className="flex-1 resize-none bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] p-6 focus:outline-none leading-relaxed"
        spellCheck
      />
    </div>
  );
}
