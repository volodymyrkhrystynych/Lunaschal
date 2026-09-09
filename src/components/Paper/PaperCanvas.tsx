import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from 'react';
import { get as idbGet, set as idbSet, del as idbDel } from 'idb-keyval';
import {
  PAGE_HEIGHT,
  PAGE_WIDTH,
  parseBuffer,
  serializeBuffer,
  serializeStrokes,
  toPageSpaceStrokes,
  type InkPalette,
  type Size,
  type Stroke,
  type StrokeTool,
  type SwipeDirection,
} from '@/lib/paper';
import { InkCanvas, type InkCanvasHandle } from '@/components/ink/InkCanvas';
import type { PageImage } from '@/lib/paperImages';

const PAGE_BG = '#ffffff';
/** What a page calls ink when a stroke does not say — which is every stroke
 * written before there was a colour picker. */
const PAPER_PALETTE: InkPalette = {
  ink: '#111111',
  highlight: '#ffe14d',
  highlightAlpha: 0.4,
};

const PAGE_SPACE: Size = { width: PAGE_WIDTH, height: PAGE_HEIGHT };

const bufferKey = (pageId: string) => `paper-page-${pageId}`;

/** One array, not a fresh `[]` per render. The ink surface re-seeds — and
 * discards its undo history — whenever the identity of `strokes` changes, so a
 * literal here would reset the page on every render its parent happened to do. */
const NO_STROKES: Stroke[] = [];

export interface PaperSaveData {
  strokes: string;
  width: number;
  height: number;
  snapshot: Blob;
  /** Edit counter at the moment this snapshot was taken. Passed back to
   * `markSaved` so strokes drawn while the upload was in flight are not
   * wrongly considered saved. */
  revision: number;
}

export interface PaperCanvasHandle {
  undo: () => void;
  redo: () => void;
  /** Snapshot + strokes for upload, or null if the page is not dirty. */
  getSaveData: () => Promise<PaperSaveData | null>;
  /** Clear the dirty flag and discard the local (IndexedDB) buffer, but only if
   * nothing has been drawn since `revision` was issued. */
  markSaved: (revision: number) => void;
  /** Treat the page as changed without a stroke being drawn. A picture added,
   * moved or deleted changes what the page looks like, and the snapshot is what
   * the explorer grid and the Journal filmstrip show — without this, a page
   * whose only content is a photo has a blank thumbnail until something is
   * drawn on it. */
  markDirty: () => void;
}

interface PaperCanvasProps {
  pageId: string;
  /** Pictures pasted onto the page, drawn beneath the ink. Interaction lives in
   * the DOM overlay above this canvas, not here — see PaperImageLayer. */
  images?: PageImage[];
  initialStrokes: Stroke[];
  /** Coordinate space the stored strokes are in. Page-space rows report the
   * fixed page size; a row saved before the A4 page space reports the CSS-pixel
   * box it was drawn in, and is converted on read (see toPageSpaceStrokes). */
  initialSize: { width: number; height: number } | null;
  tool: StrokeTool;
  /** Base width for the active tool, in page units. */
  size: number;
  color?: string;
  onSwipe: (direction: SwipeDirection) => void;
  /** Two-finger tap on the page — toggles the eraser (see isTap). */
  onToggleEraser?: () => void;
  onStateChange?: (s: {
    canUndo: boolean;
    canRedo: boolean;
    dirty: boolean;
    revision: number;
  }) => void;
}

/**
 * A page of the Paper editor: the shared ink surface, plus everything that is
 * specific to a page that gets *saved* — the on-device stroke buffer, the
 * pictures painted beneath the ink, and the snapshot the explorer grid shows.
 *
 * The drawing itself — pointer capture, the stroke buffer, the painting — lives
 * in src/components/ink/InkCanvas.tsx and is shared with the newspaper reader.
 */
export const PaperCanvas = forwardRef<PaperCanvasHandle, PaperCanvasProps>(
  function PaperCanvas(
    {
      pageId,
      images,
      initialStrokes,
      initialSize,
      tool,
      size,
      color,
      onSwipe,
      onToggleEraser,
      onStateChange,
    },
    ref
  ) {
    const inkRef = useRef<InkCanvasHandle>(null);
    // Null until the on-device buffer has been looked for. Ink saved before the
    // page space existed is converted on read; a row already in page space
    // passes through untouched.
    const [seed, setSeed] = useState<Stroke[] | null>(null);
    const fromBufferRef = useRef(false);

    // Prefer an unsaved local buffer over the server copy if one exists.
    useEffect(() => {
      let cancelled = false;
      const stored = toPageSpaceStrokes(initialStrokes, initialSize);
      idbGet(bufferKey(pageId))
        .then(buf => {
          if (cancelled) return;
          const buffered = parseBuffer(buf, initialSize);
          fromBufferRef.current = Boolean(buffered);
          setSeed(buffered ?? stored);
        })
        .catch(() => {
          if (!cancelled) setSeed(stored);
        });
      return () => {
        cancelled = true;
      };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [pageId]);

    // A buffer on the device is by definition unsaved work, so the page is
    // dirty the moment it is adopted.
    useEffect(() => {
      if (seed && fromBufferRef.current) {
        fromBufferRef.current = false;
        inkRef.current?.markDirty();
      }
    }, [seed]);

    // Content arriving after mount (a refetch landing while this canvas is up)
    // still has to be converted before the ink surface can adopt it.
    const [adopted, setAdopted] = useState<Stroke[] | null>(null);
    const seenRef = useRef(initialStrokes);
    useEffect(() => {
      if (initialStrokes === seenRef.current) return;
      seenRef.current = initialStrokes;
      setAdopted(toPageSpaceStrokes(initialStrokes, initialSize));
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [initialStrokes]);

    // Decoded <img> elements, keyed by URL. A miss kicks off the load and
    // repaints when it lands, so a page with pictures fills in rather than
    // waiting on the network before showing any ink.
    const imageElsRef = useRef(new Map<string, HTMLImageElement>());
    const imageElement = (url: string): HTMLImageElement | null => {
      const cache = imageElsRef.current;
      let el = cache.get(url);
      if (!el) {
        el = new Image();
        el.onload = () => inkRef.current?.redraw();
        el.src = url;
        cache.set(url, el);
      }
      return el.complete && el.naturalWidth > 0 ? el : null;
    };

    /** Page white plus the pictures, under the ink.
     *
     * Identity changes with `images`, which is what makes the ink surface
     * repaint when a picture is added, moved or removed — and it is guarded
     * there against firing mid-stroke. */
    const paintBackdrop = useCallback(
      (ctx: CanvasRenderingContext2D, box: Size) => {
        ctx.fillStyle = PAGE_BG;
        ctx.fillRect(0, 0, box.width, box.height);
        const s = box.width / PAGE_WIDTH;
        for (const img of images ?? []) {
          const el = imageElement(img.url);
          if (!el) continue;
          ctx.save();
          // Rotate and mirror about the image's own centre, matching the
          // geometry in src/lib/paperImages.ts exactly — the overlay's CSS
          // transform and the hit test both assume it.
          ctx.translate(
            (img.x + img.width / 2) * s,
            (img.y + img.height / 2) * s
          );
          ctx.rotate((img.rotation * Math.PI) / 180);
          if (img.flipped) ctx.scale(-1, 1);
          ctx.drawImage(
            el,
            (-img.width / 2) * s,
            (-img.height / 2) * s,
            img.width * s,
            img.height * s
          );
          ctx.restore();
        }
      },
      // eslint-disable-next-line react-hooks/exhaustive-deps
      [images]
    );

    const persistBuffer = useCallback(
      (strokes: Stroke[]) => {
        idbSet(bufferKey(pageId), serializeBuffer(strokes)).catch(() => {});
      },
      [pageId]
    );

    useImperativeHandle(ref, () => ({
      undo: () => inkRef.current?.undo(),
      redo: () => inkRef.current?.redo(),
      markDirty: () => inkRef.current?.markDirty(),
      markSaved: (revision: number) => {
        const state = inkRef.current?.getState();
        // A stroke landed while the upload was in flight — the page is still
        // dirty and the buffer must survive for the next save.
        if (!state || state.revision !== revision) return;
        inkRef.current?.markSaved(revision);
        idbDel(bufferKey(pageId)).catch(() => {});
      },
      getSaveData: async () => {
        const ink = inkRef.current;
        const state = ink?.getState();
        if (!ink || !state || !state.dirty) return null;
        const snapshot = await ink.toBlob('image/png');
        if (!snapshot) return null;
        return {
          strokes: serializeStrokes(state.strokes),
          // Always the fixed page space — the size column is what tells a
          // later load whether the row needs converting.
          width: PAGE_WIDTH,
          height: PAGE_HEIGHT,
          snapshot,
          revision: state.revision,
        };
      },
    }));

    return (
      <InkCanvas
        ref={inkRef}
        space={PAGE_SPACE}
        strokes={adopted ?? seed ?? NO_STROKES}
        tool={tool}
        size={size}
        color={color}
        palette={PAPER_PALETTE}
        // The page is the screen: nothing scrolls, so a finger is free to mean
        // a page flip or an eraser toggle instead.
        touchPolicy="exclusive"
        paintBackdrop={paintBackdrop}
        onEdit={persistBuffer}
        onStateChange={onStateChange}
        onSwipe={onSwipe}
        onToggleEraser={onToggleEraser}
        className="bg-white touch-none"
      />
    );
  }
);
