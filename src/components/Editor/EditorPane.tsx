import { useEffect, useRef, useState } from 'react';
import { EditorView, basicSetup } from 'codemirror';
import { EditorState } from '@codemirror/state';
import { markdown } from '@codemirror/lang-markdown';
import { javascript } from '@codemirror/lang-javascript';
import { python } from '@codemirror/lang-python';
import { oneDark } from '@codemirror/theme-one-dark';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';

interface Props {
  filePath: string;
  pendingInsert: string | null;
  onInsertDone: () => void;
}

function getLang(path: string) {
  const ext = path.split('.').pop()?.toLowerCase() ?? '';
  if (ext === 'md' || ext === 'markdown') return markdown();
  if (ext === 'js' || ext === 'jsx') return javascript();
  if (ext === 'ts' || ext === 'tsx') return javascript({ typescript: true });
  if (ext === 'py') return python();
  return [];
}

const SAVE_DEBOUNCE_MS = 1500;

export function EditorPane({ filePath, pendingInsert, onInsertDone }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [saveStatus, setSaveStatus] = useState<'saved' | 'saving' | 'unsaved'>('saved');
  const queryClient = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ['files', 'read', filePath],
    queryFn: () => api.files.read(filePath),
    enabled: !!filePath,
  });

  const writeMutation = useMutation({
    mutationFn: (content: string) => api.files.write(filePath, content),
    onSuccess: () => {
      setSaveStatus('saved');
      queryClient.setQueryData(['files', 'read', filePath], (old: { content: string } | undefined) =>
        old ? old : undefined
      );
    },
  });

  // Build/rebuild editor when file or content changes
  useEffect(() => {
    if (!containerRef.current || data === undefined) return;

    viewRef.current?.destroy();

    const view = new EditorView({
      state: EditorState.create({
        doc: data.content,
        extensions: [
          basicSetup,
          oneDark,
          getLang(filePath),
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
    setSaveStatus('saved');

    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
      view.destroy();
    };
  }, [filePath, data?.content]);

  // Insert pending text at cursor
  useEffect(() => {
    if (!pendingInsert || !viewRef.current) return;
    const view = viewRef.current;
    const pos = view.state.selection.main.head;
    view.dispatch({
      changes: { from: pos, insert: pendingInsert },
      selection: { anchor: pos + pendingInsert.length },
    });
    view.focus();
    onInsertDone();
  }, [pendingInsert]);

  if (!filePath) {
    return (
      <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)]">
        Select a file to edit
      </div>
    );
  }

  const statusLabel = saveStatus === 'saved' ? 'Saved' : saveStatus === 'saving' ? 'Saving…' : 'Unsaved';
  const statusColor = saveStatus === 'saved' ? 'text-green-500' : saveStatus === 'saving' ? 'text-yellow-400' : 'text-[var(--color-text-muted)]';

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="flex items-center justify-between px-3 py-1 border-b border-white/10 bg-[var(--color-surface)] shrink-0">
        <span className="text-sm text-[var(--color-text-muted)] truncate">{filePath}</span>
        <span className={`text-xs ${statusColor}`}>{statusLabel}</span>
      </div>
      {isLoading ? (
        <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)]">Loading…</div>
      ) : (
        <div ref={containerRef} className="flex-1 overflow-hidden" />
      )}
    </div>
  );
}
