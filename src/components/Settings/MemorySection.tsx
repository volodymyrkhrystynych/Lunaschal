import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type MemoryRevision } from '../../hooks/api';

/**
 * The standing document the assistant keeps about the user.
 *
 * This section is what makes the assistant's `remember` tool safe to let write
 * without a confirmation card: every change it makes is visible here and every
 * previous version is one click away. Without an editor and a history, an
 * unconfirmed write into a document that rides in every system prompt would be
 * a thing the user could neither see nor take back.
 */
const SOURCE_LABEL: Record<MemoryRevision['source'], string> = {
  remember: 'assistant added a line',
  revise: 'assistant revised it',
  user: 'you edited it',
  restore: 'restored an earlier version',
};

export function MemorySection() {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState('');
  const [showHistory, setShowHistory] = useState(false);
  const [saved, setSaved] = useState(false);
  const dirtyRef = useRef(false);

  const { data: memory } = useQuery({
    queryKey: ['memory'],
    queryFn: api.memory.get,
  });
  const { data: revisions } = useQuery({
    queryKey: ['memory', 'revisions'],
    queryFn: api.memory.revisions,
    enabled: showHistory,
  });

  // Don't clobber an in-progress edit with a refetch — the assistant can write
  // to this document while the box is open.
  useEffect(() => {
    if (memory && !dirtyRef.current) setDraft(memory.content);
  }, [memory]);

  const invalidate = () => {
    dirtyRef.current = false;
    queryClient.invalidateQueries({ queryKey: ['memory'] });
  };

  const save = useMutation({
    mutationFn: (content: string) => api.memory.update(content),
    onSuccess: () => {
      invalidate();
      setSaved(true);
    },
  });

  const restore = useMutation({
    mutationFn: (id: string) => api.memory.restore(id),
    onSuccess: result => {
      setDraft(result.content);
      invalidate();
    },
  });

  // 1.5 s debounce, the writing-editor pattern.
  useEffect(() => {
    if (!memory || draft === memory.content) return;
    dirtyRef.current = true;
    setSaved(false);
    const timer = setTimeout(() => save.mutate(draft), 1500);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft, memory?.content]);

  const maxChars = memory?.maxChars ?? 4000;
  const overCap = draft.length > maxChars;

  return (
    <div className="space-y-3">
      <div>
        <p className="text-xs text-[var(--color-text-muted)] mt-1">
          One page of standing facts the assistant carries between conversations
          — names and their spellings, people, places, standing preferences. It
          writes here itself when you correct it, without asking; this is where
          you check and undo that. Spellings kept here are also what dictated
          messages get corrected against.
        </p>
      </div>

      <textarea
        aria-label="Memory"
        value={draft}
        onChange={e => setDraft(e.target.value)}
        rows={10}
        placeholder='- Their cat is called Miso (speech-to-text hears "me so")'
        className="w-full rounded bg-[var(--color-bg)] border border-white/10 px-2 py-1 text-sm font-mono text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)]"
      />

      <div className="flex items-center gap-3 text-xs text-[var(--color-text-muted)]">
        <span className={overCap ? 'text-red-400' : undefined}>
          {draft.length} / {maxChars} characters
        </span>
        {/* Fixed slot rather than conditional text, so the row doesn't reflow
            on every autosave — the paper toolbar's lesson. */}
        <span className="w-24">
          {save.isPending
            ? 'Saving…'
            : save.isError
              ? 'Save failed'
              : saved
                ? 'Saved'
                : ''}
        </span>
        <button
          type="button"
          onClick={() => setShowHistory(v => !v)}
          className="px-2 py-1 rounded bg-white/10 text-[var(--color-text)] hover:bg-white/15"
        >
          {showHistory ? 'Hide history' : 'History'}
        </button>
      </div>

      {showHistory && (
        <div className="space-y-2 pt-3 border-t border-white/10">
          {revisions?.length ? (
            revisions.map(revision => (
              <div
                key={revision.id}
                className="rounded border border-white/10 p-2 text-xs"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[var(--color-text-muted)]">
                    {new Date(revision.createdAt).toLocaleString()} ·{' '}
                    {SOURCE_LABEL[revision.source] ?? revision.source}
                    {revision.note ? `: ${revision.note}` : ''}
                  </span>
                  <button
                    type="button"
                    onClick={() => restore.mutate(revision.id)}
                    disabled={restore.isPending}
                    className="shrink-0 px-2 py-0.5 rounded bg-white/10 text-[var(--color-text)] hover:bg-white/15 disabled:opacity-50"
                  >
                    Restore
                  </button>
                </div>
                {/* The document as it stood *before* this change, which is what
                    Restore puts back. */}
                <pre className="mt-1 whitespace-pre-wrap text-[var(--color-text-muted)]">
                  {revision.content || '(empty)'}
                </pre>
              </div>
            ))
          ) : (
            <p className="text-xs text-[var(--color-text-muted)]">
              No changes yet.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
