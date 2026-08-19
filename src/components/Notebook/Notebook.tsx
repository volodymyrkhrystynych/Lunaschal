import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { NotebookEditorPane } from './NotebookEditorPane';
import { NotebookReviewSession } from './NotebookReviewSession';
import { INDEX_PATH } from '../../lib/notebookVim';

export function Notebook() {
  const [selectedPath, setSelectedPath] = useState<string>(INDEX_PATH);
  const [reviewing, setReviewing] = useState(false);
  // Vim's <BS> "go back" needs no backlink index — as long as this history
  // unwinds as many hops as the diary-jump/link-follow drilling made, it's
  // fine that nothing tracks who links to what. Every hop after the initial
  // index page happens via a link/diary jump/`:q`, so the chain is sound.
  const [history, setHistory] = useState<string[]>([]);

  // Guarantees index.md exists before the editor pane's own read query runs
  // for it — the sole entry point into a fresh notebook with no files yet.
  const ensureIndex = useQuery({
    queryKey: ['notebook', 'files', 'ensure', INDEX_PATH],
    // React Query rejects an `undefined` resolution, so map ensure()'s void
    // return to a real value.
    queryFn: () => api.notebook.files.ensure(INDEX_PATH).then(() => true),
  });

  const { data: due } = useQuery({
    queryKey: ['notebook', 'review', 'due'],
    queryFn: api.notebook.review.due,
  });
  const dueCount = due?.length ?? 0;

  if (reviewing) {
    return (
      <div className="flex-1 flex flex-col overflow-hidden">
        <div className="flex items-center justify-between px-3 py-1 border-b border-white/10 bg-[var(--color-surface)] shrink-0">
          <span className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wide">
            Notebook Review
          </span>
          <button
            onClick={() => setReviewing(false)}
            className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-1"
          >
            Back to Notebook
          </button>
        </div>
        <NotebookReviewSession onExit={() => setReviewing(false)} />
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="flex items-center justify-end px-3 py-1 border-b border-white/10 bg-[var(--color-surface)] shrink-0">
        <button
          onClick={() => setReviewing(true)}
          disabled={dueCount === 0}
          className="text-xs px-2 py-1 rounded bg-[var(--color-primary)]/20 text-[var(--color-primary)] hover:bg-[var(--color-primary)]/30 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          Review {dueCount > 0 ? `(${dueCount} due)` : ''}
        </button>
      </div>
      {ensureIndex.isSuccess ? (
        <NotebookEditorPane
          filePath={selectedPath}
          onOpenPath={path => {
            setHistory(h => [...h, selectedPath]);
            setSelectedPath(path);
          }}
          onGoBack={() => {
            setHistory(h => {
              if (h.length === 0) return h;
              setSelectedPath(h[h.length - 1]);
              return h.slice(0, -1);
            });
          }}
        />
      ) : (
        <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)]">
          {ensureIndex.isError ? "Couldn't load the notebook." : 'Loading…'}
        </div>
      )}
    </div>
  );
}
