import {
  forwardRef,
  memo,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type ReactNode,
} from 'react';
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
  undo as undoState,
  type InkPalette,
  type Size,
  type Stroke,
  type StrokePoint,
  type StrokeState,
  type StrokeTool,
} from '@/lib/ink';
import { strokePathData } from '@/lib/inkPath';
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

export interface InkSurfaceState {
  canUndo: boolean;
  canRedo: boolean;
  dirty: boolean;
  revision: number;
}

export interface InkSurfaceHandle {
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
  /** The live `<svg>`, for a surface that has to rasterize itself. */
  element: () => SVGSVGElement | null;
}

export interface InkSurfaceProps {
  /** The coordinate space the strokes are in, and the SVG's viewBox. */
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
   * policy. Deliberately not this surface: on a scrolling page it is mounted
   * only while its page is near the viewport, and the guard has to outlive
   * that. */
  guardRef?: React.RefObject<Element | null>;
  minPointDistance?: number;
  maxPointsPerStroke?: number;
  /** Drawn under the ink, inside the same SVG and the same coordinate space, so
   * it scales with the page and lands in any rasterization of it. */
  backdrop?: ReactNode;
  /** After any change to the committed strokes. */
  onEdit?: (strokes: Stroke[]) => void;
  onStateChange?: (state: InkSurfaceState) => void;
  /** Finger gestures, honoured only under the `exclusive` policy — under
   * `scroll` every finger belongs to the browser. */
  onSwipe?: (direction: SwipeDirection) => void;
  onToggleEraser?: () => void;
  className?: string;
  /** Names the ink layer apart from whatever it is drawn over. */
  label?: string;
}

/** One committed stroke.
 *
 * Memoised on the stroke object, which is immutable: erasing rebuilds only the
 * strokes it actually cut, so a scrub across one mark does not re-derive the
 * outline of every other mark on the page. */
const InkStroke = memo(function InkStroke({
  stroke,
  palette,
}: {
  stroke: Stroke;
  palette: InkPalette;
}) {
  const d = strokePathData(stroke);
  if (!d) return null;
  return (
    <path
      d={d}
      fill={strokeColor(stroke, palette)}
      opacity={
        stroke.tool === 'highlighter' ? palette.highlightAlpha : undefined
      }
    />
  );
});

/**
 * The drawing surface every ink surface in the app is made of: pointer capture,
 * the live stroke, and the SVG the ink is drawn as.
 *
 * SVG rather than a canvas because ink is read at whatever magnification the
 * reader chooses — a newspaper is pinch-zoomed to read small print, and a
 * bitmap would soften exactly when it is being looked at hardest. It also means
 * a stroke costs one DOM node rather than a page-sized bitmap, which is what
 * makes a several-hundred-page issue affordable at all.
 *
 * What varies between surfaces is deliberately small: the coordinate space, the
 * palette, what sits behind the ink, and how touches are shared with the page
 * around it.
 */
export const InkSurface = forwardRef<InkSurfaceHandle, InkSurfaceProps>(
  function InkSurface(
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
      minPointDistance = MIN_POINT_DISTANCE,
      maxPointsPerStroke,
      backdrop,
      onEdit,
      onStateChange,
      onSwipe,
      onToggleEraser,
      className = '',
      label,
    },
    ref
  ) {
    const svgRef = useRef<SVGSVGElement>(null);
    const stateRef = useRef<StrokeState>({
      strokes,
      history: [],
      redo: [],
    });
    /** What is painted. Mirrors `stateRef.current.strokes`, which stays the
     * authoritative copy because the imperative handle has to read it
     * synchronously, before React has re-rendered. */
    const [painted, setPainted] = useState<Stroke[]>(strokes);
    /** While the eraser is down: what the page would look like if it lifted,
     * and how much ink that leaves — the number a change is detected by. */
    const [erasing, setErasing] = useState<Stroke[] | null>(null);
    const erasedTo = useRef(-1);
    const dirtyRef = useRef(false);
    // Bumped on every edit so an in-flight save can tell whether the surface
    // moved on underneath it.
    const revisionRef = useRef(0);

    // Read through refs so the handlers never close over a stale value.
    const toolRef = useRef(tool);
    toolRef.current = tool;
    const sizeRef = useRef(size);
    sizeRef.current = size;
    const colorRef = useRef(color);
    colorRef.current = color;
    const spaceRef = useRef(space);
    spaceRef.current = space;

    // Live drawing scratch state.
    const drawingRef = useRef<{ pointerId: number; stroke: Stroke } | null>(
      null
    );
    /** Read by the touch guard at event time. */
    const isDrawingRef = useRef(false);
    /** The in-flight stroke's own element, written straight to the DOM. A
     * React render per pointer move would re-derive every other stroke's
     * outline on the page; this touches one attribute on one node. */
    const liveRef = useRef<SVGPathElement>(null);
    const liveFrame = useRef(0);
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
    // Ring showing the eraser footprint while erasing (a colourless eraser is
    // otherwise invisible). Driven imperatively to avoid a React re-render on
    // every pointer move.
    const eraserCursorRef = useRef<SVGCircleElement>(null);

    const exclusive = touchPolicy === 'exclusive';
    useInkTouchPolicy({
      policy: touchPolicy,
      marking: tool !== null,
      guardRef: guardRef ?? svgRef,
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

    // Convert a pointer event into the stroke space. Read per axis from the
    // live bounding rect, which is exactly how the viewBox maps back out, so
    // the ink lands under the nib whatever size the box currently is.
    const toLogical = (e: PointerEvent): StrokePoint => {
      const el = svgRef.current!;
      const rect = el.getBoundingClientRect();
      const { width, height } = spaceRef.current;
      const fx = rect.width ? (e.clientX - rect.left) / rect.width : 0;
      const fy = rect.height ? (e.clientY - rect.top) / rect.height : 0;
      return {
        x: fx * width,
        y: fy * height,
        pressure: e.pressure > 0 ? e.pressure : 0.5,
      };
    };

    const commitState = (next: StrokeState) => {
      stateRef.current = next;
      setPainted(next.strokes);
    };

    const afterEdit = () => {
      dirtyRef.current = true;
      revisionRef.current += 1;
      onEdit?.(stateRef.current.strokes);
      notify();
    };

    /** Paint the stroke in flight, coalesced to one rebuild per frame. */
    const drawLive = () => {
      if (liveFrame.current) return;
      liveFrame.current = requestAnimationFrame(() => {
        liveFrame.current = 0;
        const el = liveRef.current;
        const d = drawingRef.current;
        if (!el) return;
        if (!d || d.stroke.tool === 'eraser') {
          el.removeAttribute('d');
          return;
        }
        el.setAttribute('d', strokePathData(d.stroke));
        el.setAttribute('fill', strokeColor(d.stroke, palette));
        el.setAttribute(
          'opacity',
          d.stroke.tool === 'highlighter' ? String(palette.highlightAlpha) : '1'
        );
      });
    };

    const clearLive = () => {
      cancelAnimationFrame(liveFrame.current);
      liveFrame.current = 0;
      liveRef.current?.removeAttribute('d');
    };

    /** The eraser tip, in the page's own units — so it is the size of the ink
     * it will remove, at whatever magnification the page is being read at. */
    const moveEraserCursor = (at: StrokePoint) => {
      const el = eraserCursorRef.current;
      if (!el) return;
      el.setAttribute('cx', String(at.x));
      el.setAttribute('cy', String(at.y));
      el.setAttribute('r', String(sizeRef.current / 2));
      el.style.opacity = '1';
    };

    const hideEraserCursor = () => {
      const el = eraserCursorRef.current;
      if (el) el.style.opacity = '0';
    };

    useEffect(() => () => cancelAnimationFrame(liveFrame.current), []);

    // Adopt content that arrives after mount. The initial state is seeded at
    // construction, so this fires only on a real change — a refetch landing
    // while this surface is already up, which used to be dropped on the floor.
    const seededRef = useRef(strokes);
    useEffect(() => {
      if (strokes === seededRef.current) return;
      seededRef.current = strokes;
      // Unsaved ink outranks anything the server has to say — unless the owner
      // above is the one holding the strokes, in which case it is never behind.
      if (dirtyRef.current && !adoptWhileDirty) return;
      // A stroke in progress lives in its own element and its own ref, so
      // adopting here cannot disturb it; but the strokes arriving do not
      // include it, and replacing the state mid-stroke would make the commit
      // land on top of content the user has not seen resolve.
      if (drawingRef.current) return;
      commitState({ strokes, history: [], redo: [] });
      notify();
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [strokes]);

    // --- pointer handlers ---
    const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
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
      if (native.isPrimary === false || drawingRef.current) return;
      // Pen / mouse: draw (or erase).
      e.preventDefault();
      svgRef.current?.setPointerCapture(native.pointerId);
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
      if (stroke.tool === 'eraser') moveEraserCursor(stroke.points[0]);
      else drawLive();
    };

    const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
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
        moveEraserCursor(d.stroke.points[d.stroke.points.length - 1]);
        // The eraser changes many strokes at once, so show the result of
        // lifting now rather than the eraser's own path. Most moves cross
        // nothing, so only a set that actually changed is pushed into state.
        const survivors = eraseStroke(
          { ...stateRef.current, history: [], redo: [] },
          simplifyStroke(d.stroke, minPointDistance)
        ).strokes;
        const left = pointCount(survivors);
        if (left === pointCount(stateRef.current.strokes)) {
          // Crossed nothing yet.
          if (erasedTo.current !== -1) {
            erasedTo.current = -1;
            setErasing(null);
          }
        } else if (left !== erasedTo.current) {
          erasedTo.current = left;
          setErasing(survivors);
        }
        return;
      }
      drawLive();
    };

    const finishDrawing = (pointerId: number, commit: boolean) => {
      const d = drawingRef.current;
      if (!d || d.pointerId !== pointerId) return;
      drawingRef.current = null;
      isDrawingRef.current = false;
      hideEraserCursor();
      clearLive();
      erasedTo.current = -1;
      setErasing(null);
      if (!commit) return;
      const simplified = simplifyStroke(d.stroke, minPointDistance);
      if (d.stroke.tool === 'eraser') {
        // The eraser stroke is consumed: it removes intersected ink and is not
        // stored. It may alter many strokes, so the undo/redo model uses full
        // snapshots.
        const after = eraseStroke(stateRef.current, simplified);
        // A scrub that crossed nothing is not an edit, and should not cost an
        // undo step or leave the page reading as unsaved.
        //
        // Counted in points, not strokes. Erasing the tail of a stroke leaves
        // it one stroke — as does erasing nothing — so comparing stroke counts
        // silently threw those rubs away. Points are exact: the eraser drops
        // precisely the ones it covered, and splits the rest into runs.
        if (pointCount(after.strokes) === pointCount(stateRef.current.strokes))
          return;
        commitState(after);
      } else {
        // Store the simplified stroke, not the raw pointer firehose: rounded and
        // with sub-unit moves dropped. Skipping this is what let a densely
        // written page grow to megabytes of JSON and get rejected with a 413.
        commitState(commitStroke(stateRef.current, simplified));
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

    const onPointerUp = (e: React.PointerEvent<SVGSVGElement>) => {
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

    const onPointerCancel = (e: React.PointerEvent<SVGSVGElement>) => {
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
      commitState(next);
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
      element: () => svgRef.current,
    }));

    const shown = erasing ?? painted;

    return (
      <div className="relative w-full h-full">
        <svg
          ref={svgRef}
          className={`w-full h-full block select-none ${className}`}
          viewBox={`0 0 ${space.width} ${space.height}`}
          // The box is always the space's own aspect, and mapping both axes
          // straight onto it is what makes a pointer land exactly where the
          // viewBox puts the ink back.
          preserveAspectRatio="none"
          style={{ touchAction: touchActionFor(touchPolicy) }}
          aria-label={label}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerCancel}
          // Only where nothing scrolls. On a scrolling surface the box moves
          // under the pointer as a matter of course, and ending a stroke every
          // time the nib crossed a page gutter would be a bug you could only
          // reproduce on the hardware.
          onPointerLeave={exclusive ? onPointerUp : undefined}
        >
          {backdrop != null && <g data-ink-backdrop="">{backdrop}</g>}
          <g data-ink-strokes="">
            {shown.map((stroke, i) => (
              <InkStroke key={i} stroke={stroke} palette={palette} />
            ))}
            <path ref={liveRef} />
          </g>
          {/* Eraser footprint, positioned imperatively and in page units —
           * drawn last so it sits over the ink it is about to take. Its outline
           * does not scale, because it is a cursor rather than part of the
           * drawing. */}
          <circle
            ref={eraserCursorRef}
            r={0}
            fill="#a3a3a3"
            fillOpacity={0.2}
            stroke="#737373"
            strokeOpacity={0.7}
            strokeWidth={2}
            vectorEffect="non-scaling-stroke"
            pointerEvents="none"
            style={{ opacity: 0, transition: 'opacity 80ms' }}
          />
        </svg>
      </div>
    );
  }
);

/** How much ink a set of strokes holds. The eraser removes points, so this is
 * what tells "it rubbed something out" from "it passed over blank page" — and
 * it stays right when a rub shortens a stroke rather than removing or splitting
 * one. */
function pointCount(strokes: Stroke[]): number {
  let n = 0;
  for (const s of strokes) n += s.points.length;
  return n;
}
