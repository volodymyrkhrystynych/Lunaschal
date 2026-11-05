import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type WritingNote } from '../../hooks/api';

interface Props {
  noteId: string;
  onRestored: (note: WritingNote) => void;
}

const SOURCE_LABEL: Record<string, string> = {
  edit: 'Before your edits',
  accept: 'Before an accepted proposal',
  restore: 'Before a restore',
};

/** A note's earlier versions (backend/writing/notes.py). Each one is the note
 * as it stood *before* a change, so restoring the newest 'accept' row undoes a
 * discussion proposal the author confirmed. Restoring records the current text
 * first, so a restore is itself undoable. */
export function NoteHistory({ noteId, onRestored }: Props) {
  const queryClient = useQueryClient();
  const { data: revisions, isLoading } = useQuery({
    queryKey: ['writing', 'note-revisions', noteId],
    queryFn: () => api.writing.noteRevisions(noteId),
  });

  const restore = useMutation({
    mutationFn: (revisionId: string) =>
      api.writing.restoreNoteRevision(noteId, revisionId),
    onSuccess: note => {
      queryClient.invalidateQueries({ queryKey: ['writing'] });
      onRestored(note);
    },
  });

  if (isLoading) {
    return (
      <div className="p-4 text-xs text-[var(--color-text-muted)]">Loading…</div>
    );
  }
  if (!revisions?.length) {
    return (
      <div className="p-4 text-xs text-[var(--color-text-muted)]">
        No earlier versions yet.
      </div>
    );
  }
  return (
    <ul className="p-2 space-y-2 overflow-y-auto">
      {revisions.map(rev => (
        <li
          key={rev.id}
          className="rounded border border-white/10 p-2 text-xs space-y-1"
        >
          <div className="flex items-center justify-between gap-2">
            <span className="text-[var(--color-text-muted)]">
              {SOURCE_LABEL[rev.source] ?? rev.source} ·{' '}
              {new Date(rev.createdAt).toLocaleString()}
            </span>
            <button
              onClick={() => restore.mutate(rev.id)}
              disabled={restore.isPending}
              className="px-2 py-0.5 rounded bg-[var(--color-primary)]/20 text-[var(--color-primary)] hover:bg-[var(--color-primary)]/30 disabled:opacity-50"
            >
              Restore
            </button>
          </div>
          <p className="whitespace-pre-wrap text-[var(--color-text)] line-clamp-4">
            {rev.content || '(empty)'}
          </p>
        </li>
      ))}
    </ul>
  );
}
