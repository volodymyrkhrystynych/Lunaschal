import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../hooks/api';
import { formatNoteCreatedAt } from '../lib/notesToSelf';

/** Persistent top-right button (mounted once in App.tsx, like SttPanel is
 * persistent at the bottom) showing how many notes to self are due for
 * review. Notes are only ever created from chat ("note to self", via
 * backend/delegate/tools.py's create_note_to_self) — this is purely the
 * review side. */
export function NoteReviewButton() {
  const [open, setOpen] = useState(false);
  const { data: due } = useQuery({
    queryKey: ['notes', 'due'],
    queryFn: api.notes.due,
    refetchOnReconnect: 'always',
  });
  const dueCount = due?.length ?? 0;

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        disabled={dueCount === 0}
        className="fixed top-2 right-3 z-40 text-xs px-2 py-1 rounded bg-[var(--color-surface)] border border-white/10 text-[var(--color-text-muted)] hover:border-[var(--color-primary)] hover:text-[var(--color-text)] disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:border-white/10 transition-colors"
      >
        Review{dueCount > 0 ? ` (${dueCount} due)` : ''}
      </button>
      {open && <NoteReviewModal onClose={() => setOpen(false)} />}
    </>
  );
}

function NoteReviewModal({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const { data: due, isLoading } = useQuery({
    queryKey: ['notes', 'due'],
    queryFn: api.notes.due,
  });
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState('');
  const [showHistory, setShowHistory] = useState(false);

  // Always the soonest-due note. Dismissing or editing invalidates the due
  // query, which either drops this one off the front (dismiss advanced its
  // schedule past "now") or refetches its new text (edit) — either way the
  // next render just reads whatever is now first, no local index to track.
  const note = due?.[0] ?? null;

  const invalidateDue = () =>
    queryClient.invalidateQueries({ queryKey: ['notes', 'due'] });

  const dismiss = useMutation({
    mutationFn: (id: string) => api.notes.dismiss(id),
    onSuccess: () => {
      invalidateDue();
      setShowHistory(false);
    },
  });

  const save = useMutation({
    mutationFn: ({ id, content }: { id: string; content: string }) =>
      api.notes.update(id, content),
    onSuccess: () => {
      invalidateDue();
      setEditing(false);
    },
  });

  // Fetched whenever a note is showing, not gated on `showHistory` — the
  // toggle that sets it is only rendered once revisions are known to be
  // non-empty, so gating the fetch on it would mean the toggle could never
  // appear in the first place.
  const { data: revisions } = useQuery({
    queryKey: ['notes', note?.id, 'revisions'],
    queryFn: () => api.notes.revisions(note!.id),
    enabled: !!note,
  });

  const startEditing = () => {
    if (!note) return;
    setEditText(note.content);
    setEditing(true);
  };

  return (
    <div
      role="dialog"
      aria-label="Review note to self"
      className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        onClick={e => e.stopPropagation()}
        className="bg-[var(--color-surface)] rounded-lg border border-white/10 max-w-md w-full flex flex-col overflow-hidden"
      >
        <div className="flex items-center gap-2 px-4 py-3 border-b border-white/10">
          <h2 className="text-sm font-medium text-[var(--color-text)]">
            Note to self
          </h2>
          <span className="flex-1" />
          <button
            type="button"
            onClick={onClose}
            className="px-2 py-0.5 rounded text-xs text-[var(--color-text-muted)] hover:bg-white/10"
          >
            Close
          </button>
        </div>

        <div className="p-4 flex flex-col gap-3">
          {isLoading ? (
            <p className="text-sm text-[var(--color-text-muted)]">Loading…</p>
          ) : !note ? (
            <p className="text-sm text-[var(--color-text-muted)]">
              All caught up — nothing due for review.
            </p>
          ) : (
            <>
              <p className="text-xs text-[var(--color-text-muted)]">
                Created {formatNoteCreatedAt(note.createdAt)}
              </p>

              {editing ? (
                <textarea
                  autoFocus
                  value={editText}
                  onChange={e => setEditText(e.target.value)}
                  rows={4}
                  className="w-full bg-[var(--color-bg)] border border-white/10 rounded p-2 text-sm text-[var(--color-text)] resize-none"
                />
              ) : (
                <p className="text-sm text-[var(--color-text)] whitespace-pre-wrap">
                  {note.content}
                </p>
              )}

              {revisions && revisions.length > 0 && (
                <div className="text-xs">
                  <button
                    type="button"
                    onClick={() => setShowHistory(s => !s)}
                    className="text-[var(--color-text-muted)] hover:text-[var(--color-text)] underline underline-offset-2"
                  >
                    {showHistory ? 'Hide' : 'Show'} edit history (
                    {revisions.length})
                  </button>
                  {showHistory && (
                    <ul className="mt-2 flex flex-col gap-2">
                      {revisions.map(r => (
                        <li
                          key={r.id}
                          className="border-l-2 border-white/10 pl-2 text-[var(--color-text-muted)]"
                        >
                          <div>{formatNoteCreatedAt(r.createdAt)}</div>
                          <div className="whitespace-pre-wrap">{r.content}</div>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}

              <div className="flex items-center gap-2 pt-1">
                {editing ? (
                  <>
                    <button
                      type="button"
                      disabled={!editText.trim() || save.isPending}
                      onClick={() =>
                        save.mutate({ id: note.id, content: editText })
                      }
                      className="text-xs px-2 py-1 rounded bg-[var(--color-primary)]/20 text-[var(--color-primary)] hover:bg-[var(--color-primary)]/30 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                    >
                      Save
                    </button>
                    <button
                      type="button"
                      onClick={() => setEditing(false)}
                      className="text-xs px-2 py-1 rounded text-[var(--color-text-muted)] hover:bg-white/10"
                    >
                      Cancel
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      type="button"
                      onClick={startEditing}
                      className="text-xs px-2 py-1 rounded text-[var(--color-text-muted)] hover:bg-white/10"
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      disabled={dismiss.isPending}
                      onClick={() => dismiss.mutate(note.id)}
                      className="text-xs px-2 py-1 rounded bg-[var(--color-primary)]/20 text-[var(--color-primary)] hover:bg-[var(--color-primary)]/30 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                    >
                      Dismiss
                    </button>
                  </>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
