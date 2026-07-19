import { useEffect, useRef, useState } from 'react';
import { EditorView, basicSetup } from 'codemirror';
import { EditorState } from '@codemirror/state';
import { markdown } from '@codemirror/lang-markdown';
import { oneDark } from '@codemirror/theme-one-dark';
import { Vim, vim } from '@replit/codemirror-vim';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';

interface Props {
  filePath: string;
  onExit: () => void;
}

const SAVE_DEBOUNCE_MS = 1500;

// Vim.defineEx registers into a process-global Ex-command table (shared by
// every vim() extension instance on the page, not scoped to one EditorView),
// so :w/:q/:wq handlers are registered once at module scope and look up the
// save/exit callbacks for whichever EditorView they were invoked on via this
// map, populated by each NotebookEditorPane instance on mount/unmount.
// cm.cm6 (confirmed via @replit/codemirror-vim's own type defs: the CodeMirror
// adapter class has a `cm6: EditorView` property) gives the real CM6 view the
// ex command fired in.
const editorCallbacks = new Map<
  EditorView,
  { save: () => void; exit: () => void }
>();

let exCommandsRegistered = false;
function registerVimExCommandsOnce() {
  if (exCommandsRegistered) return;
  exCommandsRegistered = true;
  Vim.defineEx('write', 'w', cm => {
    editorCallbacks.get(cm.cm6)?.save();
  });
  Vim.defineEx('quit', 'q', cm => {
    const cb = editorCallbacks.get(cm.cm6);
    cm.cm6.contentDOM.blur();
    cb?.exit();
  });
  Vim.defineEx('wq', undefined, cm => {
    const cb = editorCallbacks.get(cm.cm6);
    cb?.save();
    cm.cm6.contentDOM.blur();
    cb?.exit();
  });
}
registerVimExCommandsOnce();

export function NotebookEditorPane({ filePath, onExit }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [saveStatus, setSaveStatus] = useState<'saved' | 'saving' | 'unsaved'>(
    'saved'
  );
  const queryClient = useQueryClient();

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

  const writeMutation = useMutation({
    mutationFn: (content: string) =>
      api.notebook.files.write(filePath, content),
    onSuccess: () => setSaveStatus('saved'),
  });

  const saveNow = () => {
    if (!viewRef.current) return;
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    setSaveStatus('saving');
    writeMutation.mutate(viewRef.current.state.doc.toString());
  };

  // Build/rebuild editor when file or content changes
  useEffect(() => {
    if (!containerRef.current || data === undefined) return;

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
          EditorView.lineWrapping,
          EditorView.updateListener.of(update => {
            if (!update.docChanged) return;
            setSaveStatus('unsaved');
            if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
            saveTimerRef.current = setTimeout(() => {
              setSaveStatus('saving');
              writeMutation.mutate(update.state.doc.toString());
            }, SAVE_DEBOUNCE_MS);
          }),
          EditorView.theme({
            '&': { height: '100%', fontSize: '13px' },
            '.cm-scroller': { overflow: 'auto' },
          }),
        ],
      }),
      parent: containerRef.current,
    });

    viewRef.current = view;
    editorCallbacks.set(view, { save: saveNow, exit: onExit });
    setSaveStatus('saved');
    view.focus();

    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
      editorCallbacks.delete(view);
      view.destroy();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filePath, data?.content]);

  // Keep the exit callback fresh without rebuilding the editor.
  useEffect(() => {
    if (!viewRef.current) return;
    const cb = editorCallbacks.get(viewRef.current);
    if (cb) cb.exit = onExit;
  }, [onExit]);

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
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="flex items-center justify-between px-3 py-1 border-b border-white/10 bg-[var(--color-surface)] shrink-0">
        <span className="text-sm text-[var(--color-text-muted)] truncate">
          {filePath}
        </span>
        <div className="flex items-center gap-3">
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
    </div>
  );
}
