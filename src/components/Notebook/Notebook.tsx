import { useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import {
  useShortcuts,
  useShortcutScope,
} from '../../shortcuts/ShortcutProvider';
import { NotebookEditorPane } from './NotebookEditorPane';
import type { NotebookPaneHandle } from './NotebookEditorPane';
import { NotebookReviewSession } from './NotebookReviewSession';
import { INDEX_PATH } from '../../lib/notebookVim';
import { buildIndexContent, treeEntriesFor } from '../../lib/notebookIndex';

// Same step the Fanfic reader scrolls its chapter by, so W/S feels the same
// in both places.
const SCROLL_STEP_PX = 120;

export function Notebook() {
  const [selectedPath, setSelectedPath] = useState<string>(INDEX_PATH);
  const [reviewing, setReviewing] = useState(false);
  // Whether the editor currently owns the keyboard. Starts false: arriving on
  // the Notebook tab used to drop you straight into index.md's vim buffer,
  // where every app shortcut is swallowed and `:q` — with nowhere further back
  // to go — re-opened the page you were already on. Now the tab opens with the
  // note merely *shown*, `nav.in` steps into it, and `:q` on the index steps
  // back out.
  const [editing, setEditing] = useState(false);
  const paneRef = useRef<NotebookPaneHandle | null>(null);
  const { setLevel } = useShortcuts();
  // Vim's <BS> "go back" needs no backlink index — as long as this history
  // unwinds as many hops as the diary-jump/link-follow drilling made, it's
  // fine that nothing tracks who links to what. Every hop after the initial
  // index page happens via a link/diary jump/`:q`, so the chain is sound.
  const [history, setHistory] = useState<string[]>([]);
  const queryClient = useQueryClient();

  useShortcutScope(1, {
    drillIn: () => {
      setEditing(true);
      paneRef.current?.focus();
      return true;
    },
    // The editor is a single document, not a list — W/S scroll it rather than
    // moving a selection, which is what makes a note readable without a mouse.
    scrollDown: () => paneRef.current?.scrollBy(SCROLL_STEP_PX),
    scrollUp: () => paneRef.current?.scrollBy(-SCROLL_STEP_PX),
  });

  // Creates index.md if it's missing, then refreshes the generated file-tree
  // block inside it. Everything the user wrote outside that block survives —
  // see src/lib/notebookIndex.ts.
  //
  // This has to finish *before* the editor pane reads the file, or CodeMirror
  // seeds from the pre-refresh text and shows a tree missing whatever note was
  // just created. Two things enforce that ordering: the initial render is gated
  // on the query below, and every later return to the index awaits `syncIndex`
  // before switching `selectedPath`. Priming the read cache with the exact
  // content we wrote also matters, because the pane seeds the editor once per
  // file and would otherwise take a stale cache entry over what's on disk.
  const syncIndex = async () => {
    await api.notebook.files.ensure(INDEX_PATH);
    const [tree, current] = await Promise.all([
      api.notebook.files.tree(),
      api.notebook.files.read(INDEX_PATH),
    ]);
    const next = buildIndexContent(
      current.content,
      treeEntriesFor(tree.entries, INDEX_PATH)
    );
    if (next !== current.content) {
      await api.notebook.files.write(INDEX_PATH, next);
    }
    queryClient.setQueryData(['notebook', 'files', 'read', INDEX_PATH], {
      content: next,
    });
    return true;
  };

  // Guarantees index.md exists and is up to date before the editor pane's own
  // read query runs for it — the sole entry point into a fresh notebook.
  //
  // Deliberately keyed outside the ['notebook'] namespace: every auto-save
  // invalidates that whole prefix, which would re-run a full recursive tree
  // walk on each 1.5s save tick while you type. Re-syncing is driven
  // explicitly by navigateTo instead, which is the only moment the tree can
  // actually be out of date.
  const ensureIndex = useQuery({
    queryKey: ['notebook-index-sync'],
    // React Query rejects an `undefined` resolution, hence syncIndex's `true`.
    queryFn: syncIndex,
  });

  // Every caller is a vim command run inside the buffer (link follow, diary
  // jump, `:find`), so the keyboard was already in the editor and stays there.
  const openPath = (path: string) => {
    setEditing(true);
    setHistory(h => [...h, selectedPath]);
    void navigateTo(path);
  };

  // `:q` on the index. The pane has already blurred CodeMirror; this puts the
  // shortcut level back inside the tab, so W/S scroll the note, `nav.out`
  // reaches the sidebar, and `nav.in` steps back into the buffer.
  const exitEditor = () => {
    setEditing(false);
    setLevel(1);
  };

  // Deliberately not folded into the setHistory updater: navigateTo writes to
  // disk, and StrictMode invokes a state updater twice.
  const goBack = () => {
    if (history.length === 0) return;
    setHistory(h => h.slice(0, -1));
    void navigateTo(history[history.length - 1]);
  };

  // Returning to the index is the moment its tree can be out of date: the hop
  // that led away from it is exactly the one that creates new notes.
  const navigateTo = async (path: string) => {
    if (path === INDEX_PATH) {
      try {
        await syncIndex();
      } catch {
        // A failed refresh must not strand the user on the page they were
        // leaving — the index still opens, just showing its previous tree.
      }
    }
    setSelectedPath(path);
  };

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
          onOpenPath={openPath}
          onGoBack={goBack}
          onExit={exitEditor}
          autoFocus={editing}
          handle={paneRef}
        />
      ) : (
        <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)]">
          {ensureIndex.isError ? "Couldn't load the notebook." : 'Loading…'}
        </div>
      )}
    </div>
  );
}
