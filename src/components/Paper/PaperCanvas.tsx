import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
import { get as idbGet, set as idbSet, del as idbDel } from 'idb-keyval';
import {
  commitStroke,
  emptyStrokeState,
  eraseStroke,
  isHorizontalSwipe,
  isTap,
  PAGE_HEIGHT,
  PAGE_WIDTH,
  parseBuffer,
  redo as redoState,
  serializeBuffer,
  serializeStrokes,
  simplifyStroke,
  strokeWidth,
  toPageSpace,
  toPageSpaceStrokes,
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

// Stylus hardware buttons, as reported by PointerEvent. The inverted "eraser
// tip" of a Wacom/Surface/XP-Pen stylus arrives as button 5 (buttons bit 32);
// the barrel button as button 2 (bit 2). Apple Pencil's double-tap and squeeze
// are NOT exposed to browsers by any API, which is why the two-finger tap
// gesture below exists as the iPad equivalent.
const PEN_ERASER_BUTTON = 5;
const PEN_ERASER_BUTTONS_BIT = 32;
const PEN_BARREL_BUTTON = 2;
const PEN_BARREL_BUTTONS_BIT = 2;

const bufferKey = (pageId: string) => `paper-page-${pageId}`;

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
}

interface PaperCanvasProps {
  pageId: string;
  initialStrokes: Stroke[];
  /** Coordinate space the stored strokes are in. Page-space rows report the
   * fixed page size; a row saved before the A4 page space reports the CSS-pixel
   * box it was drawn in, and is converted on read (see toPageSpaceStrokes). */
  initialSize: { width: number; height: number } | null;
  tool: StrokeTool;
  /** Base width for the active tool, in logical pixels. */
  size: number;
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

export const PaperCanvas = forwardRef<PaperCanvasHandle, PaperCanvasProps>(
  function PaperCanvas(
    {
      pageId,
      initialStrokes,
      initialSize,
      tool,
      size,
      onSwipe,
      onToggleEraser,
      onStateChange,
    },
    ref
  ) {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const stateRef = useRef<StrokeState>(emptyStrokeState());
    const dirtyRef = useRef(false);
    // Bumped on every edit so an in-flight save can tell whether the page moved
    // on underneath it.
    const revisionRef = useRef(0);
    const toolRef = useRef(tool);
    toolRef.current = tool;
    const sizeRef = useRef(size);
    sizeRef.current = size;

    // Live drawing scratch state.
    const drawingRef = useRef<{ pointerId: number; stroke: Stroke } | null>(
      null
    );
    // Active finger contacts. Tracked as a map (not a single pointer) so a
    // two-finger tap can be told apart from a one-finger page swipe.
    const touchRef = useRef<{
      points: Map<
        number,
        { startX: number; startY: number; x: number; y: number; t0: number }
      >;
      maxFingers: number;
      allTaps: boolean;
    }>({ points: new Map(), maxFingers: 0, allTaps: true });
    // Overlay ring showing the eraser footprint while erasing (a white eraser is
    // otherwise invisible on the white page). Driven imperatively to avoid a
    // React re-render on every pointer move.
    const eraserCursorRef = useRef<HTMLDivElement>(null);

    const notify = () => {
      onStateChange?.({
        canUndo: stateRef.current.history.length > 0,
        canRedo: stateRef.current.redo.length > 0,
        dirty: dirtyRef.current,
        revision: revisionRef.current,
      });
    };

    /** The tool a pen event asks for via its hardware buttons, if any. Holding
     * the barrel button or flipping to the eraser tip erases for the duration of
     * the stroke without touching the selected tool. */
    const penButtonTool = (e: PointerEvent): StrokeTool | null => {
      if (e.pointerType !== 'pen') return null;
      const eraser =
        e.button === PEN_ERASER_BUTTON ||
        (e.buttons & PEN_ERASER_BUTTONS_BIT) !== 0 ||
        e.button === PEN_BARREL_BUTTON ||
        (e.buttons & PEN_BARREL_BUTTONS_BIT) !== 0;
      return eraser ? 'eraser' : null;
    };

    const ctxOf = () => canvasRef.current?.getContext('2d') ?? null;

    // Page units -> on-screen CSS pixels. A single factor for both axes: the
    // canvas box is fitted to the page's A4 aspect by PaperEditor, so there is
    // no separate sx/sy to drift apart and squash the ink.
    const scale = () => {
      const c = canvasRef.current;
      if (!c || !c.clientWidth) return 1;
      return c.clientWidth / PAGE_WIDTH;
    };

    const paintOf = (tool: StrokeTool) =>
      tool === 'eraser' ? PAGE_BG : tool === 'highlighter' ? HIGHLIGHT : INK;

    // Draw a full stroke. Pen tapers with pen pressure per segment; the eraser
    // paints the page background at a constant width; the highlighter is one
    // translucent flat pass (a single path so overlapping segments don't stack
    // alpha into dark blobs).
    const drawStroke = (ctx: CanvasRenderingContext2D, stroke: Stroke) => {
      const s = scale();
      const pts = stroke.points;
      const paint = paintOf(stroke.tool);
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      ctx.strokeStyle = paint;
      ctx.fillStyle = paint;

      if (stroke.tool === 'highlighter') {
        ctx.save();
        ctx.globalAlpha = HIGHLIGHT_ALPHA;
        ctx.lineWidth = stroke.size * s;
        ctx.beginPath();
        ctx.moveTo(pts[0].x * s, pts[0].y * s);
        if (pts.length === 1) {
          ctx.lineTo(pts[0].x * s + 0.01, pts[0].y * s);
        } else {
          for (let i = 1; i < pts.length; i++) {
            ctx.lineTo(pts[i].x * s, pts[i].y * s);
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
        ctx.arc(p.x * s, p.y * s, (w * s) / 2, 0, Math.PI * 2);
        ctx.fill();
        return;
      }
      for (let i = 1; i < pts.length; i++) {
        const a = pts[i - 1];
        const b = pts[i];
        const w = usePressure
          ? strokeWidth(stroke.size, b.pressure)
          : stroke.size;
        ctx.lineWidth = w * s;
        ctx.beginPath();
        ctx.moveTo(a.x * s, a.y * s);
        ctx.lineTo(b.x * s, b.y * s);
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

    // Convert a pointer event to a page-space point. Goes through the live
    // bounding rect, so the ink lands under the stylus whatever the fit scale
    // is (and keeps working while the page box animates or the sidebar opens).
    const toLogical = (e: PointerEvent): StrokePoint => {
      const c = canvasRef.current!;
      const rect = c.getBoundingClientRect();
      const { x, y } = toPageSpace(
        e.clientX - rect.left,
        e.clientY - rect.top,
        {
          width: rect.width,
          height: rect.height,
        }
      );
      const pressure = e.pressure > 0 ? e.pressure : 0.5;
      return { x, y, pressure };
    };

    const persistBuffer = () => {
      idbSet(
        bufferKey(pageId),
        serializeBuffer(stateRef.current.strokes)
      ).catch(() => {});
    };

    const moveEraserCursor = (e: PointerEvent) => {
      const el = eraserCursorRef.current;
      const c = canvasRef.current;
      if (!el || !c) return;
      const rect = c.getBoundingClientRect();
      const d = sizeRef.current * scale();
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
      setupCanvas();
      // Ink saved before the page space existed is converted on read; a row
      // already in page space passes through untouched.
      const stored = toPageSpaceStrokes(initialStrokes, initialSize);

      // Prefer an unsaved local buffer over the server copy if one exists.
      let cancelled = false;
      idbGet(bufferKey(pageId))
        .then(buf => {
          if (cancelled) return;
          const buffered = parseBuffer(buf, initialSize);
          if (buffered) {
            stateRef.current = {
              strokes: buffered,
              history: [],
              redo: [],
            };
            dirtyRef.current = true;
          } else {
            stateRef.current = {
              strokes: stored,
              history: [],
              redo: [],
            };
            dirtyRef.current = false;
          }
          redrawAll();
          notify();
        })
        .catch(() => {
          stateRef.current = {
            strokes: stored,
            history: [],
            redo: [],
          };
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

    // Adopt content that arrives *after* mount. The effect above reads
    // initialStrokes once, so a refetch landing while this canvas is already up
    // used to be dropped on the floor — which is what kept a page blank after
    // its stale (pre-save) cache entry had seeded it. Identity is stable
    // between renders (the parent memoises it), so this only fires on real
    // changes. Never while dirty: unsaved strokes outrank anything the server
    // has to say.
    const seededRef = useRef(initialStrokes);
    useEffect(() => {
      if (initialStrokes === seededRef.current) return;
      seededRef.current = initialStrokes;
      if (dirtyRef.current) return;
      stateRef.current = {
        strokes: toPageSpaceStrokes(initialStrokes, initialSize),
        history: [],
        redo: [],
      };
      redrawAll();
      notify();
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [initialStrokes]);

    // --- pointer handlers ---
    const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
      const native = e.nativeEvent;
      if (native.pointerType === 'touch') {
        // Finger: gesture candidate. Never inks (palm rejection). Ignore while a
        // pen stroke is in progress.
        if (drawingRef.current) return;
        const t = touchRef.current;
        t.points.set(native.pointerId, {
          startX: native.clientX,
          startY: native.clientY,
          x: native.clientX,
          y: native.clientY,
          t0: performance.now(),
        });
        t.maxFingers = Math.max(t.maxFingers, t.points.size);
        return;
      }
      // Pen / mouse: draw (or erase).
      canvasRef.current?.setPointerCapture(native.pointerId);
      const stroke: Stroke = {
        tool: penButtonTool(native) ?? toolRef.current,
        size: sizeRef.current,
        points: [toLogical(native)],
      };
      drawingRef.current = { pointerId: native.pointerId, stroke };
      if (stroke.tool === 'eraser') {
        moveEraserCursor(native);
      } else {
        const ctx = ctxOf();
        if (ctx) drawStroke(ctx, stroke); // dot for a tap
      }
    };

    const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
      if (e.nativeEvent.pointerType === 'touch') {
        const t = touchRef.current.points.get(e.nativeEvent.pointerId);
        if (t) {
          t.x = e.nativeEvent.clientX;
          t.y = e.nativeEvent.clientY;
        }
        return;
      }
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
      if (d.stroke.tool === 'eraser') {
        moveEraserCursor(e.nativeEvent);
        // The real eraser changes many strokes at once; redraw the whole page
        // so the user sees the result live as they scrub.
        const eraser = simplifyStroke(d.stroke);
        const erased = eraseStroke(
          { ...stateRef.current, history: [], redo: [] },
          eraser
        );
        // Show the preview without committing: draw the erased state + eraser cursor.
        const cc = canvasRef.current;
        const ctx = ctxOf();
        if (cc && ctx) {
          ctx.fillStyle = PAGE_BG;
          ctx.fillRect(0, 0, cc.clientWidth, cc.clientHeight);
          for (const s of erased.strokes) drawStroke(ctx, s);
          // Small circle where the eraser tip is so the active area is visible.
          const last = eraser.points[eraser.points.length - 1];
          const s = scale();
          const r = (eraser.size * s) / 2;
          ctx.strokeStyle = '#888';
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.arc(last.x * s, last.y * s, r, 0, Math.PI * 2);
          ctx.stroke();
        }
        return;
      }
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
      const simplified = simplifyStroke(d.stroke);
      if (d.stroke.tool === 'eraser') {
        // The eraser stroke is consumed: it removes intersected ink and is not
        // stored. It may alter many strokes, so the undo/redo model uses
        // full snapshots.
        stateRef.current = eraseStroke(stateRef.current, simplified);
      } else {
        // Store the simplified stroke, not the raw pointer firehose: rounded to a
        // tenth of a logical pixel with sub-pixel moves dropped. Skipping this is
        // what let a densely written page grow to megabytes of JSON and get
        // rejected by the server with a 413.
        stateRef.current = commitStroke(stateRef.current, simplified);
      }
      dirtyRef.current = true;
      revisionRef.current += 1;
      persistBuffer();
      notify();
    };

    /** Resolve a finished finger gesture once the last contact lifts. */
    const endTouchGesture = (lifted: {
      startX: number;
      startY: number;
      x: number;
      y: number;
      t0: number;
    }) => {
      const t = touchRef.current;
      const dx = lifted.x - lifted.startX;
      const dy = lifted.y - lifted.startY;
      const duration = performance.now() - lifted.t0;
      t.allTaps = t.allTaps && isTap(dx, dy, duration);
      if (t.points.size > 0) return; // other fingers still down
      const { maxFingers, allTaps } = t;
      t.maxFingers = 0;
      t.allTaps = true;
      if (maxFingers >= 2) {
        // Two-finger tap: toggle the eraser. Never flips the page, so a
        // two-finger drag can become pinch-zoom later without conflict.
        if (maxFingers === 2 && allTaps) onToggleEraser?.();
        return;
      }
      if (isHorizontalSwipe(dx, dy, SWIPE_THRESHOLD)) {
        onSwipe(dx < 0 ? 'next' : 'prev');
      }
    };

    const onPointerUp = (e: React.PointerEvent<HTMLCanvasElement>) => {
      const native = e.nativeEvent;
      if (native.pointerType === 'touch') {
        const lifted = touchRef.current.points.get(native.pointerId);
        touchRef.current.points.delete(native.pointerId);
        if (lifted) {
          lifted.x = native.clientX;
          lifted.y = native.clientY;
          endTouchGesture(lifted);
        }
        return;
      }
      finishDrawing(native.pointerId);
    };

    const onPointerCancel = (e: React.PointerEvent<HTMLCanvasElement>) => {
      if (e.nativeEvent.pointerType === 'touch') {
        const t = touchRef.current;
        t.points.delete(e.nativeEvent.pointerId);
        if (t.points.size === 0) {
          t.maxFingers = 0;
          t.allTaps = true;
        }
        return;
      }
      finishDrawing(e.nativeEvent.pointerId);
    };

    // --- imperative handle ---
    useImperativeHandle(ref, () => ({
      undo: () => {
        stateRef.current = undoState(stateRef.current);
        dirtyRef.current = true;
        revisionRef.current += 1;
        persistBuffer();
        redrawAll();
        notify();
      },
      redo: () => {
        stateRef.current = redoState(stateRef.current);
        dirtyRef.current = true;
        revisionRef.current += 1;
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
              // Always the fixed page space — the size column is what tells a
              // later load whether the row needs converting.
              width: PAGE_WIDTH,
              height: PAGE_HEIGHT,
              snapshot: blob,
              revision: revisionRef.current,
            });
          }, 'image/png');
        }),
      markSaved: (revision: number) => {
        // A stroke landed while the upload was in flight — the page is still
        // dirty and the buffer must survive for the next save.
        if (revision !== revisionRef.current) return;
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
