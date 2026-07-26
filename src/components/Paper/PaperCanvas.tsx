import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
import { get as idbGet, set as idbSet, del as idbDel } from 'idb-keyval';
import {
  commitStroke,
  emptyStrokeState,
  isHorizontalSwipe,
  parseStrokes,
  redo as redoState,
  resolveSwipe,
  serializeStrokes,
  strokeWidth,
  undo as undoState,
  type Stroke,
  type StrokePoint,
  type StrokeState,
  type StrokeTool,
  type SwipeDirection,
} from '@/lib/paper';

const PAGE_BG = '#ffffff';
const INK = '#111111';
const HIGHLIGHT = '#ffe14d';
const HIGHLIGHT_ALPHA = 0.4;
const SWIPE_THRESHOLD = 60;

const bufferKey = (pageId: string) => `paper-page-${pageId}`;

export interface PaperCanvasHandle {
  undo: () => void;
  redo: () => void;
  /** Snapshot + strokes for upload, or null if the page is not dirty. */
  getSaveData: () => Promise<{
    strokes: string;
    width: number;
    height: number;
    snapshot: Blob;
  } | null>;
  /** Clear the dirty flag and discard the local (IndexedDB) buffer. */
  markSaved: () => void;
}

interface PaperCanvasProps {
  pageId: string;
  initialStrokes: Stroke[];
  /** Logical coordinate space the stored strokes live in (size at capture). */
  initialSize: { width: number; height: number } | null;
  tool: StrokeTool;
  /** Base width for the active tool, in logical pixels. */
  size: number;
  onSwipe: (direction: SwipeDirection) => void;
  onStateChange?: (s: {
    canUndo: boolean;
    canRedo: boolean;
    dirty: boolean;
  }) => void;
}

export const PaperCanvas = forwardRef<PaperCanvasHandle, PaperCanvasProps>(
  function PaperCanvas(
    { pageId, initialStrokes, initialSize, tool, size, onSwipe, onStateChange },
    ref
  ) {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const stateRef = useRef<StrokeState>(emptyStrokeState());
    const dirtyRef = useRef(false);
    const toolRef = useRef(tool);
    toolRef.current = tool;
    const sizeRef = useRef(size);
    sizeRef.current = size;

    // The fixed logical coordinate space for this page. Points are stored in it
    // regardless of the on-screen canvas size, so a reload or rotation just
    // rescales on render. Set on mount from the stored size (or current size).
    const logicalRef = useRef<{ w: number; h: number }>({ w: 1, h: 1 });

    // Live drawing scratch state.
    const drawingRef = useRef<{ pointerId: number; stroke: Stroke } | null>(
      null
    );
    const swipeRef = useRef<{
      pointerId: number;
      startX: number;
      startY: number;
    } | null>(null);
    // Overlay ring showing the eraser footprint while erasing (a white eraser is
    // otherwise invisible on the white page). Driven imperatively to avoid a
    // React re-render on every pointer move.
    const eraserCursorRef = useRef<HTMLDivElement>(null);

    const notify = () => {
      onStateChange?.({
        canUndo: stateRef.current.strokes.length > 0,
        canRedo: stateRef.current.redo.length > 0,
        dirty: dirtyRef.current,
      });
    };

    const ctxOf = () => canvasRef.current?.getContext('2d') ?? null;

    // scale factor logical -> on-screen CSS pixels
    const scale = () => {
      const c = canvasRef.current;
      if (!c) return { sx: 1, sy: 1 };
      return {
        sx: c.clientWidth / logicalRef.current.w,
        sy: c.clientHeight / logicalRef.current.h,
      };
    };

    const paintOf = (tool: StrokeTool) =>
      tool === 'eraser' ? PAGE_BG : tool === 'highlighter' ? HIGHLIGHT : INK;

    // Draw a full stroke. Pen tapers with pen pressure per segment; the eraser
    // paints the page background at a constant width; the highlighter is one
    // translucent flat pass (a single path so overlapping segments don't stack
    // alpha into dark blobs).
    const drawStroke = (ctx: CanvasRenderingContext2D, stroke: Stroke) => {
      const { sx, sy } = scale();
      const avg = (sx + sy) / 2;
      const pts = stroke.points;
      const paint = paintOf(stroke.tool);
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      ctx.strokeStyle = paint;
      ctx.fillStyle = paint;

      if (stroke.tool === 'highlighter') {
        ctx.save();
        ctx.globalAlpha = HIGHLIGHT_ALPHA;
        ctx.lineWidth = stroke.size * avg;
        ctx.beginPath();
        ctx.moveTo(pts[0].x * sx, pts[0].y * sy);
        if (pts.length === 1) {
          ctx.lineTo(pts[0].x * sx + 0.01, pts[0].y * sy);
        } else {
          for (let i = 1; i < pts.length; i++) {
            ctx.lineTo(pts[i].x * sx, pts[i].y * sy);
          }
        }
        ctx.stroke();
        ctx.restore();
        return;
      }

      const usePressure = stroke.tool === 'pen';
      if (pts.length === 1) {
        const p = pts[0];
        const w = usePressure
          ? strokeWidth(stroke.size, p.pressure)
          : stroke.size;
        ctx.beginPath();
        ctx.arc(p.x * sx, p.y * sy, (w * avg) / 2, 0, Math.PI * 2);
        ctx.fill();
        return;
      }
      for (let i = 1; i < pts.length; i++) {
        const a = pts[i - 1];
        const b = pts[i];
        const w = usePressure
          ? strokeWidth(stroke.size, b.pressure)
          : stroke.size;
        ctx.lineWidth = w * avg;
        ctx.beginPath();
        ctx.moveTo(a.x * sx, a.y * sy);
        ctx.lineTo(b.x * sx, b.y * sy);
        ctx.stroke();
      }
    };

    const redrawAll = () => {
      const c = canvasRef.current;
      const ctx = ctxOf();
      if (!c || !ctx) return;
      ctx.fillStyle = PAGE_BG;
      ctx.fillRect(0, 0, c.clientWidth, c.clientHeight);
      for (const s of stateRef.current.strokes) drawStroke(ctx, s);
    };

    // Size the backing store to the CSS box × devicePixelRatio for crisp lines.
    const setupCanvas = () => {
      const c = canvasRef.current;
      if (!c) return;
      const dpr = window.devicePixelRatio || 1;
      const w = c.clientWidth;
      const h = c.clientHeight;
      c.width = Math.round(w * dpr);
      c.height = Math.round(h * dpr);
      const ctx = c.getContext('2d');
      if (ctx) ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    // Convert a pointer event to a logical-space point.
    const toLogical = (e: PointerEvent): StrokePoint => {
      const c = canvasRef.current!;
      const rect = c.getBoundingClientRect();
      const lx = ((e.clientX - rect.left) / rect.width) * logicalRef.current.w;
      const ly = ((e.clientY - rect.top) / rect.height) * logicalRef.current.h;
      const pressure = e.pressure > 0 ? e.pressure : 0.5;
      return { x: lx, y: ly, pressure };
    };

    const persistBuffer = () => {
      idbSet(
        bufferKey(pageId),
        serializeStrokes(stateRef.current.strokes)
      ).catch(() => {});
    };

    const moveEraserCursor = (e: PointerEvent) => {
      const el = eraserCursorRef.current;
      const c = canvasRef.current;
      if (!el || !c) return;
      const rect = c.getBoundingClientRect();
      const { sx, sy } = scale();
      const d = sizeRef.current * ((sx + sy) / 2);
      el.style.width = `${d}px`;
      el.style.height = `${d}px`;
      el.style.transform = `translate(${e.clientX - rect.left - d / 2}px, ${
        e.clientY - rect.top - d / 2
      }px)`;
      el.style.opacity = '1';
    };

    const hideEraserCursor = () => {
      const el = eraserCursorRef.current;
      if (el) el.style.opacity = '0';
    };

    // --- mount / page change ---
    useEffect(() => {
      const c = canvasRef.current;
      if (!c) return;
      logicalRef.current = initialSize
        ? {
            w: initialSize.width || c.clientWidth,
            h: initialSize.height || c.clientHeight,
          }
        : { w: c.clientWidth || 1, h: c.clientHeight || 1 };
      setupCanvas();

      // Prefer an unsaved local buffer over the server copy if one exists.
      let cancelled = false;
      idbGet(bufferKey(pageId))
        .then(buf => {
          if (cancelled) return;
          if (typeof buf === 'string') {
            const parsed = parseStrokes(buf);
            stateRef.current = { strokes: parsed, redo: [] };
            dirtyRef.current = true;
          } else {
            stateRef.current = { strokes: initialStrokes, redo: [] };
            dirtyRef.current = false;
          }
          redrawAll();
          notify();
        })
        .catch(() => {
          stateRef.current = { strokes: initialStrokes, redo: [] };
          redrawAll();
          notify();
        });

      // Re-fit the backing store whenever the canvas box changes size. A
      // ResizeObserver (not window 'resize') is essential: toggling the sidebar
      // reflows this element without any viewport resize, and without re-fitting
      // the browser would stretch the old bitmap and offset every stroke. rAF
      // coalesces the burst of callbacks during the sidebar's open/close.
      let raf = 0;
      const onResize = () => {
        cancelAnimationFrame(raf);
        raf = requestAnimationFrame(() => {
          setupCanvas();
          redrawAll();
        });
      };
      const ro =
        typeof ResizeObserver !== 'undefined'
          ? new ResizeObserver(onResize)
          : null;
      ro?.observe(c);
      window.addEventListener('resize', onResize);
      return () => {
        cancelled = true;
        cancelAnimationFrame(raf);
        ro?.disconnect();
        window.removeEventListener('resize', onResize);
      };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [pageId]);

    // --- pointer handlers ---
    const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
      const native = e.nativeEvent;
      if (native.pointerType === 'touch') {
        // Finger: candidate swipe. Never inks (palm rejection). Ignore while a
        // pen stroke is in progress.
        if (drawingRef.current) return;
        swipeRef.current = {
          pointerId: native.pointerId,
          startX: native.clientX,
          startY: native.clientY,
        };
        return;
      }
      // Pen / mouse: draw.
      canvasRef.current?.setPointerCapture(native.pointerId);
      const stroke: Stroke = {
        tool: toolRef.current,
        size: sizeRef.current,
        points: [toLogical(native)],
      };
      drawingRef.current = { pointerId: native.pointerId, stroke };
      if (stroke.tool === 'eraser') moveEraserCursor(native);
      const ctx = ctxOf();
      if (ctx) drawStroke(ctx, stroke); // dot for a tap
    };

    const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
      const d = drawingRef.current;
      if (!d || e.nativeEvent.pointerId !== d.pointerId) return;
      const ctx = ctxOf();
      if (!ctx) return;
      const events =
        typeof e.nativeEvent.getCoalescedEvents === 'function'
          ? e.nativeEvent.getCoalescedEvents()
          : [e.nativeEvent];
      for (const ev of events.length ? events : [e.nativeEvent]) {
        d.stroke.points.push(toLogical(ev));
      }
      if (d.stroke.tool === 'eraser') moveEraserCursor(e.nativeEvent);
      if (d.stroke.tool === 'highlighter') {
        // The translucent stroke must be redrawn as one uniform pass, so repaint
        // the committed strokes plus the in-progress highlighter each frame.
        redrawAll();
        drawStroke(ctx, d.stroke);
      } else {
        // Opaque tools: draw only the new tail segment for low latency.
        const pts = d.stroke.points;
        const from = Math.max(1, pts.length - events.length);
        for (let i = from; i < pts.length; i++) {
          drawStroke(ctx, {
            tool: d.stroke.tool,
            size: d.stroke.size,
            points: [pts[i - 1], pts[i]],
          });
        }
      }
    };

    const finishDrawing = (pointerId: number) => {
      const d = drawingRef.current;
      if (!d || d.pointerId !== pointerId) return;
      drawingRef.current = null;
      hideEraserCursor();
      stateRef.current = commitStroke(stateRef.current, d.stroke);
      dirtyRef.current = true;
      persistBuffer();
      notify();
    };

    const onPointerUp = (e: React.PointerEvent<HTMLCanvasElement>) => {
      const native = e.nativeEvent;
      if (native.pointerType === 'touch') {
        const s = swipeRef.current;
        swipeRef.current = null;
        if (!s || s.pointerId !== native.pointerId) return;
        const dx = native.clientX - s.startX;
        const dy = native.clientY - s.startY;
        if (isHorizontalSwipe(dx, dy, SWIPE_THRESHOLD)) {
          onSwipe(dx < 0 ? 'next' : 'prev');
        }
        return;
      }
      finishDrawing(native.pointerId);
    };

    const onPointerCancel = (e: React.PointerEvent<HTMLCanvasElement>) => {
      if (e.nativeEvent.pointerType === 'touch') {
        swipeRef.current = null;
        return;
      }
      finishDrawing(e.nativeEvent.pointerId);
    };

    // --- imperative handle ---
    useImperativeHandle(ref, () => ({
      undo: () => {
        stateRef.current = undoState(stateRef.current);
        dirtyRef.current = true;
        persistBuffer();
        redrawAll();
        notify();
      },
      redo: () => {
        stateRef.current = redoState(stateRef.current);
        dirtyRef.current = true;
        persistBuffer();
        redrawAll();
        notify();
      },
      getSaveData: () =>
        new Promise(resolve => {
          const c = canvasRef.current;
          if (!c || !dirtyRef.current) {
            resolve(null);
            return;
          }
          c.toBlob(blob => {
            if (!blob) {
              resolve(null);
              return;
            }
            resolve({
              strokes: serializeStrokes(stateRef.current.strokes),
              width: Math.round(logicalRef.current.w),
              height: Math.round(logicalRef.current.h),
              snapshot: blob,
            });
          }, 'image/png');
        }),
      markSaved: () => {
        dirtyRef.current = false;
        idbDel(bufferKey(pageId)).catch(() => {});
        notify();
      },
    }));

    return (
      <div className="relative w-full h-full">
        <canvas
          ref={canvasRef}
          className="w-full h-full block bg-white touch-none select-none"
          style={{ touchAction: 'none' }}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerCancel}
          onPointerLeave={onPointerUp}
        />
        {/* Eraser footprint indicator (positioned imperatively). */}
        <div
          ref={eraserCursorRef}
          className="absolute top-0 left-0 rounded-full border-2 border-neutral-500/70 bg-neutral-400/20 pointer-events-none"
          style={{
            opacity: 0,
            transition: 'opacity 80ms',
            willChange: 'transform',
          }}
        />
      </div>
    );
  }
);
