import { useCallback, useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import {
  parseStrokes,
  resolveSwipe,
  TOOL_SIZES,
  type StrokeTool,
  type SwipeDirection,
} from '../../lib/paper';
import { PaperCanvas, type PaperCanvasHandle } from './PaperCanvas';

const TOOL_META: { id: StrokeTool; label: string; icon: string }[] = [
  { id: 'pen', label: 'Pen', icon: '🖊' },
  { id: 'highlighter', label: 'Highlighter', icon: '🖍' },
  { id: 'eraser', label: 'Eraser', icon: '⌫' },
];
const SIZE_LABELS = ['S', 'M', 'L'];

/** How long the pen must be still before the page is uploaded on its own. */
const AUTOSAVE_DELAY_MS = 1500;

interface PaperEditorProps {
  paperId: string;
  onBack: () => void;
}

export function PaperEditor({ paperId, onBack }: PaperEditorProps) {
  const queryClient = useQueryClient();
  const canvasRef = useRef<PaperCanvasHandle>(null);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [tool, setTool] = useState<StrokeTool>('pen');
  // Each tool remembers its own width (index into TOOL_SIZES[tool]).
  const [sizeIndex, setSizeIndex] = useState<Record<StrokeTool, number>>({
    pen: 1,
    highlighter: 1,
    eraser: 1,
  });
  const currentSize = TOOL_SIZES[tool][sizeIndex[tool]];
  const [canvasState, setCanvasState] = useState({
    canUndo: false,
    canRedo: false,
    dirty: false,
    revision: 0,
  });
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(false);

  // The tool to return to when the eraser is toggled back off.
  const prevToolRef = useRef<StrokeTool>('pen');
  const toggleEraser = useCallback(() => {
    setTool(current => {
      if (current === 'eraser') return prevToolRef.current;
      prevToolRef.current = current;
      return 'eraser';
    });
  }, []);

  const { data: paper } = useQuery({
    queryKey: ['paper', paperId],
    queryFn: () => api.paper.get(paperId),
  });
  const pages = paper?.pages ?? [];
  const currentPage = pages[currentIndex];

  const { data: content } = useQuery({
    queryKey: ['paper', 'page', currentPage?.id],
    queryFn: () => api.paper.getPage(currentPage!.id),
    enabled: !!currentPage?.id,
  });

  const addPage = useMutation({
    mutationFn: () => api.paper.addPage(paperId),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ['paper', paperId] }),
  });

  const archiveRequested = paper?.archiveRequested ?? false;
  const setArchive = useMutation({
    mutationFn: (requested: boolean) =>
      api.paper.setArchiveRequested(paperId, requested),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['paper', paperId] });
      queryClient.invalidateQueries({ queryKey: ['paper'] });
    },
  });

  /** Upload the current page. Never throws: a save failure must not stop the
   * caller, because unsaved strokes stay in the canvas's IndexedDB buffer and
   * trapping the user on a page that won't save only risks losing more work. */
  const saveCurrent = async (): Promise<boolean> => {
    if (!currentPage || savingRef.current) return true;
    const data = await canvasRef.current?.getSaveData();
    if (!data) return true; // nothing dirty
    savingRef.current = true;
    setSaving(true);
    try {
      await api.paper.savePage(currentPage.id, data);
      // Guarded by the revision: strokes drawn during the upload stay dirty.
      canvasRef.current?.markSaved(data.revision);
      setSaveError(null);
      // Only the grid needs refreshing. Invalidating the whole 'paper' prefix
      // would also refetch the page content being drawn on right now.
      queryClient.invalidateQueries({ queryKey: ['paper'], exact: true });
      return true;
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : 'Could not save this page');
      return false;
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  };

  // Effects and gesture callbacks read the save through a ref so they don't need
  // to be torn down and re-registered on every render.
  const saveRef = useRef(saveCurrent);
  saveRef.current = saveCurrent;

  const navigate = async (direction: SwipeDirection) => {
    const res = resolveSwipe(direction, currentIndex, pages.length);
    if (res.index === currentIndex && !res.createPage) return;
    await saveCurrent();
    if (res.createPage) {
      try {
        await addPage.mutateAsync();
      } catch (e) {
        setSaveError(e instanceof Error ? e.message : 'Could not add a page');
        return;
      }
    }
    setCurrentIndex(res.index);
  };

  const handleBack = async () => {
    await saveCurrent();
    onBack();
  };

  /** Append a page and jump to it. `pages.length` is the pre-insert count, which
   * is exactly the index the new page lands on. */
  const addNewPage = async () => {
    await saveCurrent();
    const target = pages.length;
    try {
      await addPage.mutateAsync();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : 'Could not add a page');
      return;
    }
    setCurrentIndex(target);
  };

  // Debounced autosave. Every stroke bumps `revision`, which reschedules the
  // timer, so the upload happens once the pen has been still for a moment.
  useEffect(() => {
    if (!canvasState.dirty) return;
    const timer = setTimeout(() => void saveRef.current(), AUTOSAVE_DELAY_MS);
    return () => clearTimeout(timer);
  }, [canvasState.dirty, canvasState.revision]);

  // Best-effort save when the tab is hidden (iPad app-switch, screen lock).
  useEffect(() => {
    const onHidden = () => {
      if (document.visibilityState === 'hidden') void saveRef.current();
    };
    document.addEventListener('visibilitychange', onHidden);
    return () => document.removeEventListener('visibilitychange', onHidden);
  }, []);

  // Keyboard tool switching / undo. Apple Pencil's double-tap isn't exposed to
  // browsers, so this and the canvas's two-finger tap are the ways to reach the
  // eraser without the toolbar.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (
        el &&
        (el.tagName === 'INPUT' ||
          el.tagName === 'TEXTAREA' ||
          el.isContentEditable)
      ) {
        return;
      }
      if (e.ctrlKey || e.metaKey) {
        if (e.key.toLowerCase() === 'z') {
          e.preventDefault();
          if (e.shiftKey) canvasRef.current?.redo();
          else canvasRef.current?.undo();
        }
        return;
      }
      if (e.key === 'e') {
        e.preventDefault();
        toggleEraser();
      } else if (e.key === 'p') {
        setTool('pen');
      } else if (e.key === 'h') {
        setTool('highlighter');
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [toggleEraser]);

  const initialStrokes = content ? parseStrokes(content.strokes) : [];
  const initialSize =
    content && content.width && content.height
      ? { width: content.width, height: content.height }
      : null;

  const btn =
    'px-3 py-1.5 rounded-md text-sm font-medium transition-colors disabled:opacity-40 bg-[var(--color-surface)] hover:bg-white/10';
  const toolBtn = (active: boolean) =>
    active
      ? 'px-3 py-1.5 rounded-md text-sm font-medium transition-colors bg-[var(--color-primary)] text-[var(--color-bg)]'
      : btn;

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-white/10 shrink-0">
        <button onClick={handleBack} className={btn}>
          ‹ Back
        </button>
        <button
          onClick={() => setArchive.mutate(!archiveRequested)}
          className={toolBtn(archiveRequested)}
          title={
            archiveRequested
              ? 'Flagged to move to the Journal at 4am — tap to keep here'
              : 'Move this paper to the Journal (happens at 4am)'
          }
        >
          📓 {archiveRequested ? 'To journal ✓' : 'To journal'}
        </button>
        <div className="w-px h-6 mx-1 bg-white/10" />
        {TOOL_META.map(t => (
          <button
            key={t.id}
            onClick={() => setTool(t.id)}
            className={toolBtn(tool === t.id)}
            title={t.label}
          >
            {t.icon} {t.label}
          </button>
        ))}

        {/* Size selector for the active tool */}
        <div className="flex items-center gap-1 ml-1">
          {TOOL_SIZES[tool].map((px, i) => (
            <button
              key={i}
              onClick={() => setSizeIndex(prev => ({ ...prev, [tool]: i }))}
              className={`w-8 h-8 rounded-md flex items-center justify-center transition-colors ${
                sizeIndex[tool] === i
                  ? 'bg-[var(--color-primary)]'
                  : 'bg-[var(--color-surface)] hover:bg-white/10'
              }`}
              title={`${SIZE_LABELS[i]} (${px}px)`}
            >
              <span
                className="rounded-full bg-current"
                style={{
                  width: `${Math.min(px, 18)}px`,
                  height: `${Math.min(px, 18)}px`,
                  color:
                    sizeIndex[tool] === i
                      ? 'var(--color-bg)'
                      : 'var(--color-text)',
                }}
              />
            </button>
          ))}
        </div>

        <div className="w-px h-6 mx-1 bg-white/10" />
        <button
          onClick={() => canvasRef.current?.undo()}
          disabled={!canvasState.canUndo}
          className={btn}
          title="Undo"
        >
          ↶
        </button>
        <button
          onClick={() => canvasRef.current?.redo()}
          disabled={!canvasState.canRedo}
          className={btn}
          title="Redo"
        >
          ↷
        </button>

        <div className="ml-auto flex items-center gap-2">
          {saving && <span className="text-xs opacity-60">Saving…</span>}
          {!saving && canvasState.dirty && (
            <span className="text-xs opacity-60">Unsaved</span>
          )}
          <button
            onClick={() => navigate('prev')}
            disabled={currentIndex === 0}
            className={btn}
            title="Previous page"
          >
            ‹
          </button>
          <span className="text-sm tabular-nums opacity-70 min-w-[3.5rem] text-center">
            {pages.length ? `${currentIndex + 1} / ${pages.length}` : '–'}
          </span>
          <button
            onClick={() => navigate('next')}
            disabled={currentIndex >= pages.length - 1}
            className={btn}
            title="Next page"
          >
            ›
          </button>
          <button
            onClick={addNewPage}
            disabled={addPage.isPending}
            className={btn}
            title="Add a new page at the end"
          >
            ＋ Page
          </button>
        </div>
      </div>

      {saveError && (
        <div className="flex items-center gap-3 px-3 py-2 text-sm bg-red-600/20 border-b border-red-600/40 shrink-0">
          <span className="flex-1">
            {saveError} — your strokes are still held on this device, so nothing
            is lost.
          </span>
          <button onClick={() => void saveCurrent()} className={btn}>
            Retry
          </button>
          <button onClick={() => setSaveError(null)} className={btn}>
            Dismiss
          </button>
        </div>
      )}

      {/* Drawing surface */}
      <div className="flex-1 relative overflow-hidden bg-neutral-200 flex items-stretch justify-center p-2">
        {currentPage && content ? (
          <div className="h-full w-full max-w-[1024px] bg-white shadow-md rounded-sm overflow-hidden">
            <PaperCanvas
              key={currentPage.id}
              ref={canvasRef}
              pageId={currentPage.id}
              initialStrokes={initialStrokes}
              initialSize={initialSize}
              tool={tool}
              size={currentSize}
              onSwipe={navigate}
              onToggleEraser={toggleEraser}
              onStateChange={setCanvasState}
            />
          </div>
        ) : (
          <div className="flex items-center justify-center w-full text-neutral-500">
            Loading…
          </div>
        )}
      </div>
    </div>
  );
}
