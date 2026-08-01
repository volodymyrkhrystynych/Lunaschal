import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { get as idbGet, set as idbSet, del as idbDel } from 'idb-keyval';
import { api, type PaperPageContent } from '../../hooks/api';
import {
  fitPageBox,
  PAGE_WIDTH,
  parseStrokes,
  resolveSwipe,
  saveStatusLabel,
  TOOL_SIZES,
  type StrokeTool,
  type SwipeDirection,
} from '../../lib/paper';
import {
  fitPastedImage,
  pastedFilename,
  rotateBy,
  snapRotation,
  type ImageBox,
  type PageImage,
} from '../../lib/paperImages';
import { PaperCanvas, type PaperCanvasHandle } from './PaperCanvas';
import { PaperToolPanel } from './PaperToolPanel';
import { PaperImageLayer } from './PaperImageLayer';
import { PaperImageActions } from './PaperImageActions';

/** Breathing room between the fitted page and the edge of the drawing area, so
 * the panel has somewhere to sit when it is docked. */
const PAGE_INSET = 10;

type PendingImageEdit = Partial<
  ImageBox & Pick<PageImage, 'rotation' | 'flipped' | 'locked'>
>;

const imageEditsBufferKey = (pageId: string) => `paper-page-images-${pageId}`;

/** Parse a buffered set of staged picture edits, defensively — a corrupt or
 * missing entry just means nothing is pending, never a crash. */
function parseImageEditsBuffer(raw: unknown): Record<string, PendingImageEdit> {
  if (typeof raw !== 'string' || !raw) return {};
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

interface PaperEditorProps {
  paperId: string;
  onBack: () => void;
}

export function PaperEditor({ paperId, onBack }: PaperEditorProps) {
  const queryClient = useQueryClient();
  const canvasRef = useRef<PaperCanvasHandle>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);
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
  // Select mode swaps the pen for picture handling. It is a mode rather than a
  // modifier because there is no mouse here: with a stylus there is no hover,
  // no right-click and no spare button to hold.
  const [selectMode, setSelectMode] = useState(false);
  const [selectedImageId, setSelectedImageId] = useState<string | null>(null);
  // Geometry of the drag in flight, before it is committed to pendingImageEdits.
  const [imagePreview, setImagePreview] = useState<{
    id: string;
    box: ImageBox;
  } | null>(null);
  // Picture transforms (move/resize/rotate/flip/lock), committed locally and
  // held here — like strokes, they only reach the server on Save — keyed by
  // image id so the same picture can be nudged several times before saving.
  const [pendingImageEdits, setPendingImageEdits] = useState<
    Record<string, PendingImageEdit>
  >({});
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(false);

  // The page is fitted into whatever the drawing area currently is, so its size
  // has to be measured rather than assumed (the sidebar reflows it without any
  // window resize).
  const stageRef = useRef<HTMLDivElement>(null);
  const [stage, setStage] = useState({ width: 0, height: 0 });
  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const measure = () =>
      setStage({ width: el.clientWidth, height: el.clientHeight });
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Contain-fit: the page keeps its A4 ratio and the leftover shows as bars.
  const box = fitPageBox({
    width: stage.width - PAGE_INSET * 2,
    height: stage.height - PAGE_INSET * 2,
  });

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

  /** Upload the current page: strokes plus any staged picture transforms.
   * Never throws: a save failure must not stop the caller, because unsaved
   * strokes stay in the canvas's IndexedDB buffer, staged picture edits stay in
   * `pendingImageEdits`, and trapping the user on a page that won't save only
   * risks losing more work. This is the only path that reaches the server —
   * drawing and moving pictures around no longer sync on their own, so this
   * runs on an explicit Save and on leaving the page (navigate/back/add page/
   * tab hidden), never on a timer. */
  const saveCurrent = async (): Promise<boolean> => {
    if (!currentPage || savingRef.current) return true;
    const data = await canvasRef.current?.getSaveData();
    const imageEdits = Object.entries(pendingImageEdits);
    if (!data && imageEdits.length === 0) return true; // nothing dirty
    savingRef.current = true;
    setSaving(true);
    try {
      if (data) {
        await api.paper.savePage(currentPage.id, data);
        // Guarded by the revision: strokes drawn during the upload stay dirty.
        canvasRef.current?.markSaved(data.revision);
        // Write what we just uploaded straight into the page's cache entry.
        // Without this the entry keeps whatever the page held when it was
        // opened — empty, for a page written on for the first time — and
        // coming back to it later re-seeds the canvas from that stale copy, so
        // the page reads as blank. Drawing on the blank one would then
        // overwrite the real strokes, since a save replaces the column
        // outright. setQueryData (not an invalidate) because the client
        // already knows the answer: no refetch, and nothing races the page
        // currently under the pen.
        queryClient.setQueryData<PaperPageContent>(
          ['paper', 'page', currentPage.id],
          prev => ({
            strokes: data.strokes,
            width: data.width,
            height: data.height,
            // A save uploads strokes only — pictures are owned by their own
            // endpoints — so carry across whatever the cache already holds
            // rather than blanking the list.
            images: prev?.images ?? [],
          })
        );
      }
      if (imageEdits.length > 0) {
        await Promise.all(
          imageEdits.map(([id, edit]) => api.paper.updateImage(id, edit))
        );
        setPendingImageEdits({});
        refreshPage();
      }
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

  // Best-effort save when the tab is hidden (iPad app-switch, screen lock).
  // This is the one automatic trigger left: it's a single flush on the way
  // out, not a recurring sync, so it doesn't fight with the manual-save intent
  // below — it just stops a background/close from losing an unsaved page.
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
        } else if (e.key.toLowerCase() === 's') {
          e.preventDefault();
          void saveRef.current();
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

  // SQLite hands booleans back as 0/1; the geometry module wants real booleans.
  const images = useMemo<PageImage[]>(
    () =>
      (content?.images ?? []).map(i => ({
        id: i.id,
        url: i.url,
        x: i.x,
        y: i.y,
        width: i.width,
        height: i.height,
        rotation: i.rotation,
        flipped: !!i.flipped,
        locked: !!i.locked,
        position: i.position,
      })),
    [content]
  );

  // What the canvas and the overlay actually draw: the server's list with any
  // staged-but-unsaved transform applied, then the in-flight drag on top of
  // that, so a move tracks the finger without a round trip and neither needs
  // the network to show up.
  const shownImages = useMemo(
    () =>
      images.map(i => {
        const pending = pendingImageEdits[i.id];
        const withPending = pending ? { ...i, ...pending } : i;
        return imagePreview && imagePreview.id === i.id
          ? { ...withPending, ...imagePreview.box }
          : withPending;
      }),
    [images, pendingImageEdits, imagePreview]
  );
  const selectedImage = shownImages.find(i => i.id === selectedImageId) ?? null;
  // A locked picture is deliberately invisible to the hit test, so tapping the
  // page can never reach one again. This is the way back to it.
  const lockedImages = shownImages.filter(i => i.locked);
  const imagesDirty = Object.keys(pendingImageEdits).length > 0;

  const refreshPage = () => {
    if (currentPage) {
      queryClient.invalidateQueries({
        queryKey: ['paper', 'page', currentPage.id],
      });
    }
  };

  /** Stage a picture transform locally instead of syncing it straight away —
   * like strokes, it only reaches the server on Save (or on leaving the
   * page). Merged over whatever is already pending for this image so several
   * transforms can stack before a save flushes them. */
  const commitImageEdit = (id: string, data: PendingImageEdit) => {
    setPendingImageEdits(prev => ({ ...prev, [id]: { ...prev[id], ...data } }));
    setImagePreview(null);
  };

  const removeImage = useMutation({
    mutationFn: (id: string) => api.paper.deleteImage(id),
    onSuccess: (_data, id) => {
      setSelectedImageId(null);
      // A pending transform for a picture that no longer exists would 404 the
      // next save.
      setPendingImageEdits(prev => {
        if (!(id in prev)) return prev;
        const { [id]: _removed, ...rest } = prev;
        return rest;
      });
      refreshPage();
    },
    onError: (e: Error) => setSaveError(e.message || 'Could not delete it'),
  });

  /** Read a blob's pixel size, so a pasted picture can be placed at a sane
   * scale before it is uploaded. */
  const naturalSize = (file: Blob) =>
    new Promise<{ width: number; height: number }>(resolve => {
      const url = URL.createObjectURL(file);
      const el = new Image();
      el.onload = () => {
        URL.revokeObjectURL(url);
        resolve({ width: el.naturalWidth, height: el.naturalHeight });
      };
      el.onerror = () => {
        URL.revokeObjectURL(url);
        // Fall back to a square: better a placed picture than a failed paste.
        resolve({ width: 1, height: 1 });
      };
      el.src = url;
    });

  const addImage = async (file: Blob, filename?: string) => {
    if (!currentPage) return;
    const name = filename ?? pastedFilename(file.type);
    if (!name) {
      setSaveError(`Can't put a ${file.type || 'file of that type'} on a page`);
      return;
    }
    try {
      const size = await naturalSize(file);
      const box = fitPastedImage(size.width, size.height);
      const created = await api.paper.addImage(currentPage.id, file, box, name);
      refreshPage();
      // Land in select mode with it chosen — pasting is always followed by
      // placing it.
      setSelectMode(true);
      setSelectedImageId(created.id);
    } catch (e) {
      setSaveError(
        e instanceof Error ? e.message : 'Could not add the picture'
      );
    }
  };

  const addImageRef = useRef(addImage);
  addImageRef.current = addImage;

  // Paste a picture straight onto the page. Registered on the window because
  // the canvas isn't focusable — there is nowhere for a paste to land otherwise.
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const item = Array.from(e.clipboardData?.items ?? []).find(
        i => i.kind === 'file' && i.type.startsWith('image/')
      );
      const file = item?.getAsFile();
      if (!file) return;
      e.preventDefault();
      void addImageRef.current(file);
    };
    window.addEventListener('paste', onPaste);
    return () => window.removeEventListener('paste', onPaste);
  }, []);

  // A selection belongs to one page; carrying it across would point the action
  // bar at a picture that is no longer on screen. Staged picture edits are
  // page-scoped too, but unlike strokes they have no server copy to fall back
  // on until saved, so they're reloaded from the same kind of local buffer
  // strokes use (imageEditsBufferKey) rather than simply discarded — a save
  // that failed on the way out must not quietly lose a moved picture.
  useEffect(() => {
    setSelectedImageId(null);
    setImagePreview(null);
    if (!currentPage) {
      setPendingImageEdits({});
      return;
    }
    let cancelled = false;
    idbGet(imageEditsBufferKey(currentPage.id))
      .then(raw => {
        if (cancelled) return;
        setPendingImageEdits(parseImageEditsBuffer(raw));
      })
      .catch(() => {
        if (!cancelled) setPendingImageEdits({});
      });
    return () => {
      cancelled = true;
    };
  }, [currentPage?.id]);

  // Mirror staged picture edits into IndexedDB, the same safety net the
  // stroke buffer gives drawing: a save that never reaches the server (closed
  // tab, dead network) must not silently drop a moved/rotated picture.
  useEffect(() => {
    if (!currentPage) return;
    const key = imageEditsBufferKey(currentPage.id);
    if (Object.keys(pendingImageEdits).length === 0) {
      idbDel(key).catch(() => {});
    } else {
      idbSet(key, JSON.stringify(pendingImageEdits)).catch(() => {});
    }
  }, [currentPage, pendingImageEdits]);

  // Memoised on the query result, whose identity React Query keeps stable
  // unless the data actually changed. The canvas adopts these on identity
  // change, so re-deriving them every render would re-seed it constantly and
  // throw away undo history.
  const initialStrokes = useMemo(
    () => (content ? parseStrokes(content.strokes) : []),
    [content]
  );
  const initialSize = useMemo(
    () =>
      content && content.width && content.height
        ? { width: content.width, height: content.height }
        : null,
    [content]
  );

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
        <button
          onClick={() => {
            setSelectMode(v => !v);
            setSelectedImageId(null);
          }}
          className={toolBtn(selectMode)}
          title={
            selectMode
              ? 'Back to drawing'
              : 'Select and move pictures instead of drawing'
          }
        >
          {selectMode ? '✋ Pictures' : '✋ Pictures'}
        </button>
        {selectMode && lockedImages.length > 0 && (
          <button
            onClick={() => {
              const at = lockedImages.findIndex(i => i.id === selectedImageId);
              setSelectedImageId(
                lockedImages[(at + 1) % lockedImages.length].id
              );
            }}
            className={btn}
            title="Step through locked pictures to unlock one"
          >
            🔒 {lockedImages.length}
          </button>
        )}
        <button
          onClick={() => imageInputRef.current?.click()}
          className={btn}
          title="Add a picture (or just paste one)"
        >
          🖼 Add
        </button>
        <input
          ref={imageInputRef}
          type="file"
          accept="image/png,image/jpeg,image/webp,image/gif"
          className="hidden"
          onChange={e => {
            const file = e.target.files?.[0];
            if (file) void addImage(file, file.name);
            e.target.value = '';
          }}
        />
        <div className="ml-auto flex items-center gap-2">
          {/* Fixed-width slot. The tools used to sit in this bar and this text
           * popped in and out on every autosave, reflowing the row out from
           * under the stylus — the reserved width is what keeps the bar (and
           * now the floating panel, which carries no status at all) still. */}
          <span className="text-xs opacity-60 w-16 text-right shrink-0">
            {saveStatusLabel(saving, canvasState.dirty || imagesDirty)}
          </span>
          <button
            onClick={() => void saveCurrent()}
            disabled={saving || !(canvasState.dirty || imagesDirty)}
            className={btn}
            title="Save this page (Ctrl+S) — nothing is sent to the server until you do"
          >
            💾 Save
          </button>
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

      {/* Drawing surface. The page is an A4 sheet fitted into this area and
       * centred; the bars left over on two sides are deliberate — a page that
       * stretched to the viewport changed shape with the device, and the ink
       * saved on it came back distorted. */}
      <div
        ref={stageRef}
        className="flex-1 relative overflow-hidden bg-neutral-200"
      >
        {currentPage && content && box.width > 0 ? (
          <div
            className="absolute bg-white shadow-md rounded-sm overflow-hidden"
            style={{
              left: box.left + PAGE_INSET,
              top: box.top + PAGE_INSET,
              width: box.width,
              height: box.height,
            }}
          >
            <PaperCanvas
              key={currentPage.id}
              ref={canvasRef}
              pageId={currentPage.id}
              images={shownImages}
              initialStrokes={initialStrokes}
              initialSize={initialSize}
              tool={tool}
              size={currentSize}
              onSwipe={navigate}
              onToggleEraser={toggleEraser}
              onStateChange={setCanvasState}
            />
            {/* Only mounted in select mode, so it can never swallow a stroke. */}
            {selectMode && (
              <PaperImageLayer
                images={shownImages}
                scale={box.width / PAGE_WIDTH}
                selectedId={selectedImageId}
                onSelect={setSelectedImageId}
                onPreview={(id, next) => setImagePreview({ id, box: next })}
                onCommit={(id, next) => commitImageEdit(id, next)}
              />
            )}
          </div>
        ) : (
          <div className="flex items-center justify-center w-full h-full text-neutral-500">
            Loading…
          </div>
        )}

        {selectMode && selectedImage && (
          <PaperImageActions
            image={selectedImage}
            onRotate={delta =>
              commitImageEdit(selectedImage.id, {
                rotation: snapRotation(rotateBy(selectedImage.rotation, delta)),
              })
            }
            onFlip={() =>
              commitImageEdit(selectedImage.id, {
                flipped: !selectedImage.flipped,
              })
            }
            onToggleLock={() =>
              commitImageEdit(selectedImage.id, {
                locked: !selectedImage.locked,
              })
            }
            onDelete={() => removeImage.mutate(selectedImage.id)}
          />
        )}

        <PaperToolPanel
          tool={tool}
          onToolChange={setTool}
          sizeIndex={sizeIndex[tool]}
          onSizeIndexChange={i =>
            setSizeIndex(prev => ({ ...prev, [tool]: i }))
          }
          canUndo={canvasState.canUndo}
          canRedo={canvasState.canRedo}
          onUndo={() => canvasRef.current?.undo()}
          onRedo={() => canvasRef.current?.redo()}
          bounds={stage}
        />
      </div>
    </div>
  );
}
