import { useEffect, useImperativeHandle, useRef, useState } from 'react';
// Aliased so it doesn't shadow the DOM's own KeyboardEvent.
import type { KeyboardEvent as ReactKeyboardEvent, RefObject } from 'react';
import { EditorView, basicSetup } from 'codemirror';
import { EditorState } from '@codemirror/state';
import { markdown } from '@codemirror/lang-markdown';
import { oneDark } from '@codemirror/theme-one-dark';
import { Decoration, MatchDecorator, ViewPlugin } from '@codemirror/view';
import type { DecorationSet, ViewUpdate } from '@codemirror/view';
import { Vim, vim } from '@replit/codemirror-vim';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { useNotebookWrite } from '../../offline/mutationDefaults';
import {
  INDEX_PATH,
  diaryPathFor,
  wikiLinkTargetAt,
  resolveWikiLinkPath,
  toggleCheckboxLine,
} from '../../lib/notebookVim';
import { matchesQuery } from '../../lib/notebookSearch';

/** The imperative surface the Notebook view drives from its shortcut scope:
 * the editor's focus and scroll position both live inside CodeMirror, and
 * neither is a thing a prop can express. */
export interface NotebookPaneHandle {
  focus: () => void;
  scrollBy: (delta: number) => void;
}

interface Props {
  filePath: string;
  /** Switches the open file — used by the diary-jump, wiki-link-follow, and
   * :q-goes-home vim customizations below. */
  onOpenPath: (path: string) => void;
  /** Pops one hop off the caller's drill-down history — <BS> below. */
  onGoBack: () => void;
  /** `:q` on the index page: there is nowhere further back to go, so the
   * editor releases the keyboard instead of swallowing it. */
  onExit?: () => void;
  /** Whether a freshly built editor should take the keyboard. False on the
   * view's first render, so arriving on the Notebook tab doesn't trap you in
   * index.md with no shortcut that works; true once you're actually editing,
   * so following a link or `:q`-ing back to the index keeps the cursor. */
  autoFocus?: boolean;
  handle?: RefObject<NotebookPaneHandle | null>;
}

const SAVE_DEBOUNCE_MS = 1500;

// Highlights [[Link]]/[[Link|Label]] spans so they read as distinct from
// plain text, the same job CodeMirror's markdown mode does for real markdown
// links — but this syntax is vimwiki's own, so nothing highlights it for free.
const wikiLinkMatcher = new MatchDecorator({
  regexp: /\[\[[^\]]+\]\]/g,
  decoration: () => Decoration.mark({ class: 'cm-wikilink' }),
});
const wikiLinkHighlighter = ViewPlugin.fromClass(
  class {
    decorations: DecorationSet;
    constructor(view: EditorView) {
      this.decorations = wikiLinkMatcher.createDeco(view);
    }
    update(update: ViewUpdate) {
      this.decorations = wikiLinkMatcher.updateDeco(update, this.decorations);
    }
  },
  { decorations: v => v.decorations }
);

// Vim.defineEx/defineAction register into process-global tables (shared by
// every vim() extension instance on the page, not scoped to one EditorView),
// so all of :w/:q/:wq/:diary and the <CR>/<C-Space> action overrides are
// registered once at module scope and look up the per-view callbacks for
// whichever EditorView they were invoked on via this map, populated by each
// NotebookEditorPane instance on mount/unmount. cm.cm6 (confirmed via
// @replit/codemirror-vim's own type defs: the CodeMirror adapter class has a
// `cm6: EditorView` property) gives the real CM6 view the command fired in.
const editorCallbacks = new Map<
  EditorView,
  {
    save: () => void;
    goHome: () => void;
    diary: () => void;
    openLink: (target: string) => void;
    goBack: () => void;
    find: (query: string) => void;
  }
>();

let vimCustomizationsRegistered = false;
function registerVimCustomizationsOnce() {
  if (vimCustomizationsRegistered) return;
  vimCustomizationsRegistered = true;
  Vim.defineEx('write', 'w', cm => {
    editorCallbacks.get(cm.cm6)?.save();
  });
  // Without a file tree to fall back to, "quit" means "back to the index" —
  // the same ensureAndOpen path diary/link-follow already use, so it stays
  // mounted and focused. On the index itself there is no further back, and
  // re-opening the page you are already on is what used to make `:q` look
  // like it bounced you straight back in; it releases the keyboard instead.
  Vim.defineEx('quit', 'q', cm => {
    editorCallbacks.get(cm.cm6)?.goHome();
  });
  Vim.defineEx('wq', undefined, cm => {
    const cb = editorCallbacks.get(cm.cm6);
    cb?.save();
    cb?.goHome();
  });
  // vimwiki's <Leader>w<Leader>w with the (common) mapleader set to <Space>:
  // jump to (creating if needed) today's diary note. codemirror-vim resolves
  // a full match on the first key of an ambiguous sequence rather than
  // waiting to see if a longer mapping applies (unlike real Vim), so the
  // stock <Space> -> l (move right) binding would fire before the second key
  // ever arrives — unmap it first, same as a real vimwiki setup that uses
  // <Space> as its leader.
  Vim.defineEx('diary', undefined, cm => {
    editorCallbacks.get(cm.cm6)?.diary();
  });
  // The upstream .d.ts marks `ctx` required, but the default <Space> entry
  // has no context set — passing '' wouldn't match its undefined context.
  Vim.unmap('<Space>', undefined as unknown as string);
  Vim.map('<Space>w<Space>w', ':diary<CR>', 'normal');

  // vimwiki has no :find, but the notebook needs *some* way to reach a note
  // whose name you remember and whose link you don't — the filename search that
  // used to live in the file-tree sidebar went away with the sidebar. Bound as
  // an ex-command rather than on `/` so vim's in-buffer text search keeps that
  // key, and abbreviated to :fin because that's what real vim's :fin[d] takes.
  Vim.defineEx('find', 'fin', (cm, params) => {
    editorCallbacks.get(cm.cm6)?.find((params.argString || '').trim());
  });

  // vimwiki's <CR> follows a [[Link]] under the cursor; otherwise it falls
  // through to a plain "move to the next line" (the closest approximation of
  // default normal-mode <CR> worth reproducing here — counts/folds are out
  // of scope).
  Vim.defineAction('notebookFollowLink', (cm: { cm6: EditorView }) => {
    const view = cm.cm6;
    const pos = view.state.selection.main.head;
    const line = view.state.doc.lineAt(pos);
    const col = pos - line.from;
    const target = wikiLinkTargetAt(line.text, col);
    if (target) {
      editorCallbacks.get(view)?.openLink(target);
      return;
    }
    const nextLineNo = Math.min(line.number + 1, view.state.doc.lines);
    const nextLine = view.state.doc.line(nextLineNo);
    const firstNonBlank = nextLine.text.search(/\S/);
    const to = nextLine.from + (firstNonBlank === -1 ? 0 : firstNonBlank);
    view.dispatch({ selection: { anchor: to } });
  });
  Vim.mapCommand(
    '<CR>',
    'action',
    'notebookFollowLink',
    {},
    { context: 'normal' }
  );

  // vimwiki's <C-Space> toggles the "- [ ]"/"- [x]" checkbox on the current
  // line, or turns a plain list item into an unchecked checkbox item.
  Vim.defineAction('notebookToggleCheckbox', (cm: { cm6: EditorView }) => {
    const view = cm.cm6;
    const line = view.state.doc.lineAt(view.state.selection.main.head);
    const next = toggleCheckboxLine(line.text);
    if (next === line.text) return;
    view.dispatch({ changes: { from: line.from, to: line.to, insert: next } });
  });
  Vim.mapCommand(
    '<C-Space>',
    'action',
    'notebookToggleCheckbox',
    {},
    { context: 'normal' }
  );

  // vimwiki's <BS> steps back through the pages a <CR>/diary jump drilled
  // into — no backlink index needed, just an unwind of however deep the
  // drilling went. <BS> is single-key, same as <C-Space>/<CR> above, so (unlike
  // <Space>) it doesn't hit the prefix-ambiguity problem and needs no unmap:
  // a newly mapCommand'd action already wins over the stock <BS> -> h motion.
  Vim.defineAction('notebookGoBack', (cm: { cm6: EditorView }) => {
    editorCallbacks.get(cm.cm6)?.goBack();
  });
  Vim.mapCommand('<BS>', 'action', 'notebookGoBack', {}, { context: 'normal' });
}
registerVimCustomizationsOnce();

export function NotebookEditorPane({
  filePath,
  onOpenPath,
  onGoBack,
  onExit,
  autoFocus = false,
  handle,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Read through a ref inside the build effect, which must not re-run (and
  // rebuild CodeMirror, losing the cursor) just because focus moved.
  const autoFocusRef = useRef(autoFocus);
  autoFocusRef.current = autoFocus;
  const [saveStatus, setSaveStatus] = useState<'saved' | 'saving' | 'unsaved'>(
    'saved'
  );
  const queryClient = useQueryClient();
  // Open `:find` picker: the candidate paths and the live filter over them.
  // Null when the picker is closed.
  const [finding, setFinding] = useState<{
    paths: string[];
    query: string;
    selected: number;
  } | null>(null);
  const findInputRef = useRef<HTMLInputElement | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['notebook', 'files', 'read', filePath],
    queryFn: () => api.notebook.files.read(filePath),
    enabled: !!filePath,
  });

  const reviewState = useQuery({
    queryKey: ['notebook', 'review', 'state', filePath],
    queryFn: () => api.notebook.review.getState(filePath),
    enabled: !!filePath,
  });

  const toggleReview = useMutation({
    mutationFn: (enabled: boolean) =>
      api.notebook.review.toggle(filePath, enabled),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ['notebook', 'review', 'state', filePath],
      });
      queryClient.invalidateQueries({
        queryKey: ['notebook', 'review', 'due'],
      });
    },
  });

  // Offline-queueable: an idempotent path-keyed overwrite that replays cleanly.
  const writeMutation = useNotebookWrite({
    onSuccess: () => setSaveStatus('saved'),
  });

  const saveNow = () => {
    if (!viewRef.current) return;
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    setSaveStatus('saving');
    writeMutation.mutate({
      path: filePath,
      content: viewRef.current.state.doc.toString(),
    });
  };

  // Opens `path` (switching away from the current file), creating it as an
  // empty note first if it doesn't exist yet — shared by the diary-jump,
  // wiki-link-follow, and :q-goes-home vim customizations.
  const ensureAndOpen = async (path: string) => {
    if (path === filePath) return;
    await api.notebook.files.ensure(path);
    onOpenPath(path);
  };

  // `:q` — one hop back toward the index, and off the keyboard once there.
  // The blur is done here rather than left to the caller because CodeMirror
  // holds the focus: without it the vim keymap keeps eating every key and the
  // app's own shortcuts (tab switching, scrolling) stay unreachable.
  //
  // Deferred by a tick because codemirror-vim's ex dialog re-focuses the
  // editor the moment our handler returns (openDialog's `close()` calls
  // `me.focus()`), so blurring inline here would simply be undone.
  const goHome = () => {
    if (filePath !== INDEX_PATH) {
      void ensureAndOpen(INDEX_PATH);
      return;
    }
    setTimeout(() => viewRef.current?.contentDOM.blur(), 0);
    onExit?.();
  };

  // `:find <query>` — jump to a note by name. The tree is re-fetched per
  // invocation rather than cached, because the note you are looking for is
  // often one you created moments ago.
  const runFind = async (query: string) => {
    const { entries } = await api.notebook.files.tree();
    const paths = entries.filter(e => !e.isDir).map(e => e.path);
    const hits = paths.filter(path => matchesQuery(path, query));
    // An unambiguous query shouldn't make you confirm the only answer.
    if (hits.length === 1) {
      void ensureAndOpen(hits[0]);
      return;
    }
    setFinding({ paths, query, selected: 0 });
  };

  const closeFind = () => {
    setFinding(null);
    // Vim is modal: leaving the picker anywhere but back in the buffer would
    // strand the next keystroke.
    viewRef.current?.focus();
  };

  // Matching on the full path, not just the filename, so "diary/2026-08" is a
  // usable query — matchesQuery is a plain substring test either way.
  const findMatches = finding
    ? finding.paths.filter(path => matchesQuery(path, finding.query))
    : [];
  const findSelected = Math.min(
    finding?.selected ?? 0,
    Math.max(0, findMatches.length - 1)
  );

  const onFindKeyDown = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    const move = (delta: number) => {
      e.preventDefault();
      setFinding(f =>
        f === null
          ? f
          : {
              ...f,
              selected: Math.min(
                Math.max(0, findSelected + delta),
                Math.max(0, findMatches.length - 1)
              ),
            }
      );
    };
    // Ctrl-n/Ctrl-p alongside the arrows: the Pocket 2's arrow keys are an
    // awkward reach, and this is the pair vim users already have in muscle
    // memory from wildmenu completion.
    if (e.key === 'ArrowDown' || (e.ctrlKey && e.key === 'n')) return move(1);
    if (e.key === 'ArrowUp' || (e.ctrlKey && e.key === 'p')) return move(-1);
    if (e.key === 'Enter') {
      e.preventDefault();
      const path = findMatches[findSelected];
      if (!path) return;
      setFinding(null);
      void ensureAndOpen(path);
      return;
    }
    if (e.key === 'Escape') {
      e.preventDefault();
      closeFind();
    }
  };

  useEffect(() => {
    if (finding) findInputRef.current?.focus();
  }, [finding !== null]);

  // Build the editor once the file's content has loaded, and rebuild only when
  // the *file* changes — NOT when `data.content` changes. Each auto-save's
  // write mutation invalidates ['notebook'], refetching this file; that brings
  // `data.content` up to the just-saved text (a new value vs. before), so
  // depending on it would tear down and recreate CodeMirror on every save,
  // resetting the cursor to the start. `dataReady` flips undefined→defined once
  // per file, so we seed the editor exactly once and leave it alone thereafter.
  const dataReady = data !== undefined;
  useEffect(() => {
    if (!containerRef.current || !dataReady) return;

    viewRef.current?.destroy();

    const view = new EditorView({
      state: EditorState.create({
        doc: data.content,
        extensions: [
          // vim() must come before basicSetup so its keymap sees keys first.
          vim(),
          basicSetup,
          oneDark,
          markdown(),
          wikiLinkHighlighter,
          EditorView.lineWrapping,
          EditorView.updateListener.of(update => {
            if (!update.docChanged) return;
            setSaveStatus('unsaved');
            if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
            saveTimerRef.current = setTimeout(() => {
              setSaveStatus('saving');
              writeMutation.mutate({
                path: filePath,
                content: update.state.doc.toString(),
              });
            }, SAVE_DEBOUNCE_MS);
          }),
          EditorView.theme({
            '&': { height: '100%', fontSize: '13px' },
            '.cm-scroller': { overflow: 'auto' },
            '.cm-wikilink': {
              color: 'var(--color-primary)',
              textDecoration: 'underline',
            },
          }),
        ],
      }),
      parent: containerRef.current,
    });

    viewRef.current = view;
    editorCallbacks.set(view, {
      save: saveNow,
      goHome,
      diary: () => ensureAndOpen(diaryPathFor()),
      openLink: target => ensureAndOpen(resolveWikiLinkPath(target, filePath)),
      goBack: onGoBack,
      find: query => void runFind(query),
    });
    setSaveStatus('saved');
    if (autoFocusRef.current) view.focus();

    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
      editorCallbacks.delete(view);
      view.destroy();
      viewRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filePath, dataReady]);

  // Keep the callbacks fresh (identity + closed-over filePath) without
  // rebuilding the editor.
  useEffect(() => {
    if (!viewRef.current) return;
    const cb = editorCallbacks.get(viewRef.current);
    if (!cb) return;
    cb.goHome = goHome;
    cb.diary = () => ensureAndOpen(diaryPathFor());
    cb.openLink = target =>
      ensureAndOpen(resolveWikiLinkPath(target, filePath));
    cb.goBack = onGoBack;
    cb.find = query => void runFind(query);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onOpenPath, onGoBack, filePath]);

  useImperativeHandle(
    handle,
    () => ({
      focus: () => viewRef.current?.focus(),
      scrollBy: (delta: number) =>
        viewRef.current?.scrollDOM.scrollBy({ top: delta, behavior: 'smooth' }),
    }),
    []
  );

  if (!filePath) return null;

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
    <div className="flex-1 flex flex-col overflow-hidden relative">
      <div className="flex items-center justify-between px-3 py-1 border-b border-white/10 bg-[var(--color-surface)] shrink-0">
        <span className="text-sm text-[var(--color-text-muted)] truncate">
          {filePath}
        </span>
        <div className="flex items-center gap-3">
          {/* `:q` is now the way out of the editor as well as the way back to
              the index, and neither is guessable from an empty page. */}
          <span className="text-xs text-[var(--color-text-muted)] hidden sm:inline">
            {filePath === INDEX_PATH
              ? ':q to leave the editor'
              : ':q for the index'}
          </span>
          <label className="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)] cursor-pointer select-none">
            <input
              type="checkbox"
              checked={reviewState.data?.enabled ?? false}
              onChange={e => toggleReview.mutate(e.target.checked)}
            />
            Review
          </label>
          <span className={`text-xs ${statusColor}`}>{statusLabel}</span>
        </div>
      </div>
      {isLoading ? (
        <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)]">
          Loading…
        </div>
      ) : (
        <div
          ref={containerRef}
          data-vim-editor
          className="flex-1 overflow-hidden"
        />
      )}
      {finding && (
        <div
          className="absolute inset-0 z-10 flex items-start justify-center pt-16 bg-black/50"
          onMouseDown={e => {
            if (e.target === e.currentTarget) closeFind();
          }}
        >
          <div className="w-[min(32rem,90%)] rounded border border-white/10 bg-[var(--color-surface)] shadow-lg overflow-hidden">
            <input
              ref={findInputRef}
              value={finding.query}
              onChange={e =>
                setFinding(f =>
                  f === null ? f : { ...f, query: e.target.value, selected: 0 }
                )
              }
              onKeyDown={onFindKeyDown}
              placeholder="Find a note…"
              className="w-full px-3 py-2 bg-transparent text-sm text-[var(--color-text)] border-b border-white/10 outline-none"
            />
            <ul className="max-h-72 overflow-y-auto">
              {findMatches.length === 0 ? (
                <li className="px-3 py-2 text-xs text-[var(--color-text-muted)]">
                  No notes match.
                </li>
              ) : (
                findMatches.map((path, i) => (
                  <li key={path}>
                    <button
                      type="button"
                      onMouseDown={e => {
                        e.preventDefault();
                        setFinding(null);
                        void ensureAndOpen(path);
                      }}
                      className={`w-full text-left px-3 py-1.5 text-sm truncate ${
                        i === findSelected
                          ? 'bg-[var(--color-primary)]/25 text-[var(--color-text)]'
                          : 'text-[var(--color-text-muted)]'
                      }`}
                    >
                      {path}
                    </button>
                  </li>
                ))
              )}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}
