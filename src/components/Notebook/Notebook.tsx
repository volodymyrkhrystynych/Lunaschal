import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, type FileEntry } from '../../hooks/api';
import { NotebookTree } from './NotebookTree';
import { NotebookEditorPane } from './NotebookEditorPane';
import { NotebookPreviewPane } from './NotebookPreviewPane';
import { NotebookReviewSession } from './NotebookReviewSession';

export function Notebook() {
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [focusedEntry, setFocusedEntry] = useState<FileEntry | null>(null);
  const [reviewing, setReviewing] = useState(false);

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
    <div className="flex-1 flex overflow-hidden">
      <div className="w-56 shrink-0 border-r border-white/10 bg-[var(--color-surface)] overflow-hidden flex flex-col">
        <NotebookTree
          selectedPath={selectedPath}
          onSelectFile={setSelectedPath}
          onFocusEntry={setFocusedEntry}
        />
      </div>
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
        {selectedPath ? (
          <NotebookEditorPane
            filePath={selectedPath}
            onExit={() => setSelectedPath(null)}
          />
        ) : (
          <NotebookPreviewPane entry={focusedEntry} />
        )}
      </div>
    </div>
  );
}
