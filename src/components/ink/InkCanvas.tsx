import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
import {
  commitStroke,
  emptyStrokeState,
  eraseStroke,
  isHorizontalSwipe,
  isTap,
  MIN_POINT_DISTANCE,
  redo as redoState,
  simplifyStroke,
  strokeColor,
  strokeWidth,
  undo as undoState,
  type InkPalette,
  type Size,
  type Stroke,
  type StrokePoint,
  type StrokeState,
  type StrokeTool,
} from '@/lib/ink';
import {
  touchActionFor,
  useInkTouchPolicy,
  type TouchPolicy,
} from './useInkTouchPolicy';

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

export type SwipeDirection = 'next' | 'prev';

export interface InkCanvasState {
  canUndo: boolean;
  canRedo: boolean;
  dirty: boolean;
  revision: number;
}

export interface InkCanvasHandle {
  undo: () => void;
  redo: () => void;
  /** The committed strokes, plus the counters a caller needs to save them
   * without racing an edit that lands mid-upload. */
  getState: () => { strokes: Stroke[]; dirty: boolean; revision: number };
  /** Treat the surface as changed without a stroke being drawn — a picture
   * added or moved changes what the page looks like. */
  markDirty: () => void;
  /** Clear the dirty flag, but only if nothing has been drawn since `revision`
   * was issued. */
  markSaved: (revision: number) => void;
  toBlob: (type?: string) => Promise<Blob | null>;
  redraw: () => void;
}

export interface InkCanvasProps {
  /** The coordinate space the strokes are in. Everything stored is in these
   * units, and the transform to the screen is a single uniform scale. */
  space: Size;
  /** Strokes to draw. Identity changes are what trigger a re-seed. */
  strokes: Stroke[];
  /** Re-seed from `strokes` even when this surface has unsaved edits.
   *
   * On when the owner above holds every committed stroke and is always current
   * (the newspaper reader, whose Undo lives outside this component). Off when
   * the strokes prop is a *server* copy that can only ever be behind what is on
   * screen — the Paper editor, where adopting mid-edit would throw away unsaved
   * ink. */
  adoptWhileDirty?: boolean;
  /** The active tool, or null for a surface in a read/no-marking mode. */
  tool: StrokeTool | null;
  /** Base width for the active tool, in the space's own units. */
  size: number;
  /** Colour for new strokes. Absent means the palette's own ink. */
  color?: string;
  palette: InkPalette;
  touchPolicy: TouchPolicy;
  /** The element carrying the non-passive touchmove guard under the `scroll`
   * policy. Deliberately not this canvas: on a scrolling surface the canvas is
   * mounted only while its page is near the viewport, and the guard has to
   * outlive that. */
  guardRef?: React.RefObject<HTMLElement | null>;
  /** Cap on the backing store's pixel ratio. A broadsheet at full devicePixel-
   * Ratio is tens of megabytes a page. */
  maxPixelRatio?: number;
  minPointDistance?: number;
  maxPointsPerStroke?: number;
  /** Painted under the ink on every full redraw. The default clears to
   * transparent, which is what a surface drawn over something else needs;
   * Paper fills its page white and draws its pictures. */
  paintBackdrop?: (ctx: CanvasRenderingContext2D, box: Size) => void;
  /** After any change to the committed strokes. */
  onEdit?: (strokes: Stroke[]) => void;
  onStateChange?: (state: InkCanvasState) => void;
  /** Finger gestures, honoured only under the `exclusive` policy — under
   * `scroll` every finger belongs to the browser. */
  onSwipe?: (direction: SwipeDirection) => void;
  onToggleEraser?: () => void;
  className?: string;
  /** Names the ink layer apart from whatever it is drawn over. */
  label?: string;
}

/**
 * The drawing surface every ink surface in the app is made of: pointer capture,
 * the live stroke buffer, and the canvas painting.
 *
 * Generalized out of the Paper editor's canvas, which is where all of this was
 * worked out — the coalesced-event batching, the tail-only paint that keeps a
 * pen feeling attached to the nib, the single-pass highlighter that stops
 * overlapping segments stacking alpha into dark blobs, and the two guards that
 * stop an unrelated repaint wiping a stroke that has not committed yet.
 *
 * What varies between surfaces is deliberately small: the coordinate space, the
 * palette, what sits behind the ink, and how touches are shared with the page
 * around it.
 */
export const InkCanvas = forwardRef<InkCanvasHandle, InkCanvasProps>(
  function InkCanvas(
    {
      space,
      strokes,
      adoptWhileDirty = false,
      tool,
      size,
      color,
      palette,
      touchPolicy,
      guardRef,
      maxPixelRatio,
      minPointDistance = MIN_POINT_DISTANCE,
      maxPointsPerStroke,
      paintBackdrop,
      onEdit,
      onStateChange,
      onSwipe,
      onToggleEraser,
      className = '',
      label,
    },
    ref
  ) {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const stateRef = useRef<StrokeState>(emptyStrokeState());
    const dirtyRef = useRef(false);
    // Bumped on every edit so an in-flight save can tell whether the surface
    // moved on underneath it.
    const revisionRef = useRef(0);

    // Read through refs so the paint helpers never close over a stale value.
    const toolRef = useRef(tool);
    toolRef.current = tool;
    const sizeRef = useRef(size);
    sizeRef.current = size;
    const colorRef = useRef(color);
    colorRef.current = color;
    const spaceRef = useRef(space);
    spaceRef.current = space;
    const paletteRef = useRef(palette);
    paletteRef.current = palette;
    const backdropRef = useRef(paintBackdrop);
    backdropRef.current = paintBackdrop;

    // Live drawing scratch state.
    const drawingRef = useRef<{ pointerId: number; stroke: Stroke } | null>(
      null
    );
    /** Read by the touch guard at event time. */
    const isDrawingRef = useRef(false);
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
    // Overlay ring showing the eraser footprint while erasing (a colourless
    // eraser is otherwise invisible). Driven imperatively to avoid a React
    // re-render on every pointer move.
    const eraserCursorRef = useRef<HTMLDivElement>(null);

    const exclusive = touchPolicy === 'exclusive';
    useInkTouchPolicy({
      policy: touchPolicy,
      marking: tool !== null,
      guardRef: guardRef ?? canvasRef,
      drawingRef: isDrawingRef,
    });

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

    // Space units -> on-screen CSS pixels. A single factor for both axes: the
    // canvas box is fitted to the space's own aspect by the surface above, so
    // there is no separate sx/sy to drift apart and squash the ink.
    const scale = () => {
      const c = canvasRef.current;
      if (!c || !c.clientWidth || !spaceRef.current.width) return 1;
      return c.clientWidth / spaceRef.current.width;
    };

    // Draw a full stroke. Pen tapers with pen pressure per segment; the
    // highlighter is one translucent flat pass (a single path so overlapping
    // segments don't stack alpha into dark blobs).
    //
    // The eraser is never drawn. It removes ink geometrically and is not stored
    // — which is also what lets this canvas be transparent, since there is no
    // "paint over it in the background colour" anywhere in the paint path.
    const drawStroke = (ctx: CanvasRenderingContext2D, stroke: Stroke) => {
      const s = scale();
      const pts = stroke.points;
      if (!pts.length) return;
      const paint = strokeColor(stroke, paletteRef.current);
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      ctx.strokeStyle = paint;
      ctx.fillStyle = paint;

      if (stroke.tool === 'highlighter') {
        ctx.save();
        ctx.globalAlpha = paletteRef.current.highlightAlpha;
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

    /** Whatever sits behind the ink. Everything that repaints from scratch goes
     * through this, so ink always lands on top. */
    const paintBehind = (ctx: CanvasRenderingContext2D) => {
      const c = canvasRef.current;
      if (!c) return;
      const box = { width: c.clientWidth, height: c.clientHeight };
      if (backdropRef.current) backdropRef.current(ctx, box);
      else ctx.clearRect(0, 0, box.width, box.height);
    };

    const redrawAll = () => {
      const ctx = ctxOf();
      if (!ctx) return;
      paintBehind(ctx);
      for (const s of stateRef.current.strokes) drawStroke(ctx, s);
    };

    // Size the backing store to the CSS box × devicePixelRatio for crisp lines.
    const setupCanvas = () => {
      const c = canvasRef.current;
      if (!c) return;
      let dpr = window.devicePixelRatio || 1;
      if (maxPixelRatio) dpr = Math.min(dpr, maxPixelRatio);
      const w = c.clientWidth;
      const h = c.clientHeight;
      c.width = Math.round(w * dpr);
      c.height = Math.round(h * dpr);
      const ctx = c.getContext('2d');
      if (ctx) ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    // Convert a pointer event to a point in the stroke space. Goes through the
    // live bounding rect, so the ink lands under the stylus whatever the fit
    // scale is (and keeps working while the box animates or a sidebar opens).
    const toLogical = (e: PointerEvent): StrokePoint => {
      const c = canvasRef.current!;
      const rect = c.getBoundingClientRect();
      const { width, height } = spaceRef.current;
      const fx = rect.width ? (e.clientX - rect.left) / rect.width : 0;
      const fy = rect.height ? (e.clientY - rect.top) / rect.height : 0;
      return {
        x: fx * width,
        y: fy * height,
        pressure: e.pressure > 0 ? e.pressure : 0.5,
      };
    };

    const afterEdit = () => {
      dirtyRef.current = true;
      revisionRef.current += 1;
      onEdit?.(stateRef.current.strokes);
      notify();
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

    // --- mount / seed ---
    const seededRef = useRef<Stroke[] | null>(null);
    useEffect(() => {
      const c = canvasRef.current;
      if (!c) return;
      setupCanvas();
      stateRef.current = { strokes, history: [], redo: [] };
      seededRef.current = strokes;
      redrawAll();
      notify();

      // Re-fit the backing store whenever the canvas box changes size. A
      // ResizeObserver (not window 'resize') is essential: toggling a sidebar
      // reflows this element without any viewport resize, and without re-fitting
      // the browser would stretch the old bitmap and offset every stroke. rAF
      // coalesces the burst of callbacks during an open/close.
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
        cancelAnimationFrame(raf);
        ro?.disconnect();
        window.removeEventListener('resize', onResize);
      };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // Adopt content that arrives *after* mount. The effect above reads `strokes`
    // once, so a refetch landing while this canvas is already up used to be
    // dropped on the floor — which is what kept a page blank after its stale
    // (pre-save) cache entry had seeded it.
    useEffect(() => {
      if (strokes === seededRef.current) return;
      seededRef.current = strokes;
      // Unsaved ink outranks anything the server has to say — unless the owner
      // above is the one holding the strokes, in which case it is never behind.
      if (dirtyRef.current && !adoptWhileDirty) return;
      // A stroke in progress is painted straight onto the canvas before it is
      // committed (see onPointerMove) and isn't part of stateRef yet, so a
      // reseed here would repaint over it and erase everything drawn so far.
      // Bail and let the commit's own paint stand.
      if (drawingRef.current) return;
      stateRef.current = { strokes, history: [], redo: [] };
      redrawAll();
      notify();
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [strokes]);

    // Repaint when what sits behind the ink changes. Guarded the same way: a
    // stroke in progress is on the canvas but not yet in stateRef, so a redraw
    // triggered by something as unrelated as a picture finishing its upload
    // would wipe it until the stroke completes.
    useEffect(() => {
      if (drawingRef.current) return;
      redrawAll();
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [paintBackdrop, space.width, space.height, palette]);

    // --- pointer handlers ---
    const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
      const native = e.nativeEvent;
      if (toolRef.current === null) return; // read mode: nothing marks
      if (native.pointerType === 'touch') {
        // A finger never inks, on any surface — that is the palm rejection.
        // Under `exclusive` it becomes a gesture candidate instead; under
        // `scroll` it belongs to the browser and is not tracked at all.
        if (!exclusive || drawingRef.current) return;
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
      // Only an explicitly secondary pointer is refused. `isPrimary` is always
      // populated on a real PointerEvent, but absent on a synthetic one, and
      // "undefined" must not read as "not primary".
      if (e.isPrimary === false || drawingRef.current) return;
      // Pen / mouse: draw (or erase).
      e.preventDefault();
      canvasRef.current?.setPointerCapture(native.pointerId);
      const drawn = penButtonTool(native) ?? toolRef.current;
      const stroke: Stroke = {
        tool: drawn,
        size: sizeRef.current,
        points: [toLogical(native)],
        // The eraser lays down no ink, so it carries no colour — and neither
        // does a stroke left at the surface's default, which is what keeps ink
        // drawn before there was a picker rendering as it always has.
        ...(drawn !== 'eraser' && colorRef.current
          ? { color: colorRef.current }
          : {}),
      };
      drawingRef.current = { pointerId: native.pointerId, stroke };
      isDrawingRef.current = true;
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
        if (
          maxPointsPerStroke &&
          d.stroke.points.length >= maxPointsPerStroke
        ) {
          break;
        }
        d.stroke.points.push(toLogical(ev));
      }
      if (d.stroke.tool === 'eraser') {
        moveEraserCursor(e.nativeEvent);
        // The real eraser changes many strokes at once; redraw the whole
        // surface so the user sees the result live as they scrub.
        const eraser = simplifyStroke(d.stroke, minPointDistance);
        const erased = eraseStroke(
          { ...stateRef.current, history: [], redo: [] },
          eraser
        );
        paintBehind(ctx);
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
            ...d.stroke,
            points: [pts[i - 1], pts[i]],
          });
        }
      }
    };

    const finishDrawing = (pointerId: number, commit: boolean) => {
      const d = drawingRef.current;
      if (!d || d.pointerId !== pointerId) return;
      drawingRef.current = null;
      isDrawingRef.current = false;
      hideEraserCursor();
      if (!commit) {
        redrawAll();
        return;
      }
      const simplified = simplifyStroke(d.stroke, minPointDistance);
      if (d.stroke.tool === 'eraser') {
        // The eraser stroke is consumed: it removes intersected ink and is not
        // stored. It may alter many strokes, so the undo/redo model uses full
        // snapshots.
        const before = stateRef.current.strokes;
        const after = eraseStroke(stateRef.current, simplified);
        // Either way the surface is repainted, to clear the tip ring the
        // preview left on it.
        if (after.strokes.length === before.length) {
          // A scrub that crossed nothing is not an edit, and should not cost an
          // undo step or leave the page reading as unsaved.
          redrawAll();
          return;
        }
        stateRef.current = after;
        redrawAll();
      } else {
        // Store the simplified stroke, not the raw pointer firehose: rounded and
        // with sub-unit moves dropped. Skipping this is what let a densely
        // written page grow to megabytes of JSON and get rejected with a 413.
        //
        // No repaint: the stroke is already on the canvas, drawn tail-first as
        // it was made, and a full redraw of a dense page costs a visible hitch
        // at the end of every stroke.
        stateRef.current = commitStroke(stateRef.current, simplified);
      }
      afterEdit();
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
        onSwipe?.(dx < 0 ? 'next' : 'prev');
      }
    };

    const onPointerUp = (e: React.PointerEvent<HTMLCanvasElement>) => {
      const native = e.nativeEvent;
      if (native.pointerType === 'touch') {
        if (!exclusive) return;
        const lifted = touchRef.current.points.get(native.pointerId);
        touchRef.current.points.delete(native.pointerId);
        if (lifted) {
          lifted.x = native.clientX;
          lifted.y = native.clientY;
          endTouchGesture(lifted);
        }
        return;
      }
      finishDrawing(native.pointerId, true);
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
      // Under `exclusive` a cancelled stroke is ink the user drew and can see,
      // so it is kept. Under `scroll` a cancel means the OS took the pointer to
      // scroll with, and half a stray line dragged across the page is worse
      // than no line at all.
      finishDrawing(e.nativeEvent.pointerId, exclusive);
    };

    // --- imperative handle ---
    const rewind = (next: StrokeState) => {
      stateRef.current = next;
      redrawAll();
      afterEdit();
    };

    useImperativeHandle(ref, () => ({
      undo: () => rewind(undoState(stateRef.current)),
      redo: () => rewind(redoState(stateRef.current)),
      getState: () => ({
        strokes: stateRef.current.strokes,
        dirty: dirtyRef.current,
        revision: revisionRef.current,
      }),
      markDirty: () => {
        // No onEdit: the strokes have not changed, so anything mirroring them
        // already matches. The revision still moves, or a save already in
        // flight would clear the dirty flag with a snapshot taken before.
        dirtyRef.current = true;
        revisionRef.current += 1;
        notify();
      },
      markSaved: (revision: number) => {
        // A stroke landed while the upload was in flight — still dirty.
        if (revision !== revisionRef.current) return;
        dirtyRef.current = false;
        notify();
      },
      toBlob: (type = 'image/png') =>
        new Promise<Blob | null>(resolve => {
          const c = canvasRef.current;
          if (!c) return resolve(null);
          c.toBlob(blob => resolve(blob), type);
        }),
      redraw: redrawAll,
    }));

    return (
      <div className="relative w-full h-full">
        <canvas
          ref={canvasRef}
          className={`w-full h-full block select-none ${className}`}
          aria-label={label}
          style={{ touchAction: touchActionFor(touchPolicy) }}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerCancel}
          // Only where nothing scrolls. On a scrolling surface the box moves
          // under the pointer as a matter of course, and ending a stroke every
          // time the nib crossed a page gutter would be a bug you could only
          // reproduce on the hardware.
          onPointerLeave={exclusive ? onPointerUp : undefined}
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
