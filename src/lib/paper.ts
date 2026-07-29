// Pure, node-testable logic for the Paper drawing view. Keeping this out of the
// canvas component lets it be unit-tested without a real 2D context (jsdom has
// none). See src/lib/paper.test.ts.

export interface StrokePoint {
  x: number;
  y: number;
  pressure: number;
}

export type StrokeTool = 'pen' | 'highlighter' | 'eraser';

export const STROKE_TOOLS: readonly StrokeTool[] = [
  'pen',
  'highlighter',
  'eraser',
];

export interface Stroke {
  tool: StrokeTool;
  /** Base stroke width in logical pixels (pen pressure modulates around it). */
  size: number;
  points: StrokePoint[];
}

/** Fallback width when a stored stroke has a missing/invalid size. */
export const DEFAULT_STROKE_SIZE = 4;

/** Selectable widths per tool (Small / Medium / Large). */
export const TOOL_SIZES: Record<StrokeTool, readonly number[]> = {
  pen: [2, 4, 7],
  highlighter: [14, 22, 34],
  eraser: [16, 30, 50],
};

/** Undo/redo model: the current strokes plus snapshots of previous states.
 * Snapshots let an erase operation — which can modify many strokes at once —
 * be undone as a single action, unlike the old per-stroke stack. */
export interface StrokeState {
  strokes: Stroke[];
  history: Stroke[][];
  redo: Stroke[][];
}

export const emptyStrokeState = (): StrokeState => ({
  strokes: [],
  history: [],
  redo: [],
});

/** Commit a finished stroke. Any pending redo history is discarded, matching
 * the usual undo/redo contract (a new edit forks the timeline). */
export function commitStroke(state: StrokeState, stroke: Stroke): StrokeState {
  return {
    strokes: [...state.strokes, stroke],
    history: [...state.history, state.strokes],
    redo: [],
  };
}

export function undo(state: StrokeState): StrokeState {
  const prev = state.history[state.history.length - 1];
  if (!prev) return state;
  return {
    strokes: prev,
    history: state.history.slice(0, -1),
    redo: [...state.redo, state.strokes],
  };
}

export function redo(state: StrokeState): StrokeState {
  const next = state.redo[state.redo.length - 1];
  if (!next) return state;
  return {
    strokes: next,
    history: [...state.history, state.strokes],
    redo: state.redo.slice(0, -1),
  };
}

/** Squared distance from point (px,py) to segment a-b. */
function dist2ToSegment(
  px: number,
  py: number,
  ax: number,
  ay: number,
  bx: number,
  by: number
): number {
  const dx = bx - ax;
  const dy = by - ay;
  const l2 = dx * dx + dy * dy;
  if (l2 === 0) return (px - ax) ** 2 + (py - ay) ** 2;
  let t = ((px - ax) * dx + (py - ay) * dy) / l2;
  t = Math.max(0, Math.min(1, t));
  const cx = ax + t * dx;
  const cy = ay + t * dy;
  return (px - cx) ** 2 + (py - cy) ** 2;
}

/** Is a point within `radius` logical pixels of any segment of the eraser? */
function pointHitByEraser(
  px: number,
  py: number,
  eraser: Stroke,
  radius: number
): boolean {
  const r2 = radius * radius;
  const pts = eraser.points;
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1];
    const b = pts[i];
    if (dist2ToSegment(px, py, a.x, a.y, b.x, b.y) <= r2) return true;
  }
  if (pts.length === 1) {
    const a = pts[0];
    const dx = px - a.x;
    const dy = py - a.y;
    if (dx * dx + dy * dy <= r2) return true;
  }
  return false;
}

/** Remove the portion of a stroke that falls under the eraser, splitting what
 * remains into one or more contiguous kept pieces. Preserves the original
 * stroke's tool and size. */
function eraseFromStroke(
  stroke: Stroke,
  eraser: Stroke,
  radius: number
): Stroke[] {
  const out: Stroke[] = [];
  let run: StrokePoint[] = [];
  for (const p of stroke.points) {
    if (pointHitByEraser(p.x, p.y, eraser, radius)) {
      if (run.length) {
        out.push({ ...stroke, points: run });
        run = [];
      }
    } else {
      run.push(p);
    }
  }
  if (run.length) out.push({ ...stroke, points: run });
  return out;
}

/** Erase all parts of existing strokes that pass under the given eraser stroke.
 * The eraser stroke itself is not stored; only its effect on existing ink is.
 * Radius defaults to the eraser's own size / 2 (diameter = size). */
export function eraseStroke(
  state: StrokeState,
  eraser: Stroke,
  radius = eraser.size / 2
): StrokeState {
  const newStrokes = state.strokes.flatMap(s =>
    eraseFromStroke(s, eraser, radius)
  );
  return {
    strokes: newStrokes,
    history: [...state.history, state.strokes],
    redo: [],
  };
}

/** Minimum distance, in logical pixels, between two consecutive stored points.
 * Pointer events fire far denser than the ink needs, and every dropped point is
 * ~40 bytes off the save payload. */
export const MIN_POINT_DISTANCE = 1;

/** Decimal places kept for coordinates (a tenth of a logical pixel is well
 * below what any display can resolve) and for pressure. */
const COORD_DECIMALS = 1;
const PRESSURE_DECIMALS = 2;

const roundTo = (value: number, decimals: number): number => {
  const f = 10 ** decimals;
  return Math.round(value * f) / f;
};

const cleanPressure = (pressure: number): number =>
  Number.isFinite(pressure)
    ? roundTo(Math.min(Math.max(pressure, 0), 1), PRESSURE_DECIMALS)
    : 0.5;

/** Reduce a freshly drawn stroke to what's worth storing: coordinates rounded
 * to a tenth of a pixel and points closer together than `minDistance` dropped.
 * The first and last points are always kept so the stroke's extent is exact.
 *
 * Applied when a stroke is committed, so the in-memory state, the IndexedDB
 * buffer, and the upload all share the same compact representation. Without
 * this a densely written page serializes to megabytes of JSON. */
export function simplifyStroke(
  stroke: Stroke,
  minDistance = MIN_POINT_DISTANCE
): Stroke {
  const pts = stroke.points;
  const out: StrokePoint[] = [];
  for (let i = 0; i < pts.length; i++) {
    const p: StrokePoint = {
      x: roundTo(pts[i].x, COORD_DECIMALS),
      y: roundTo(pts[i].y, COORD_DECIMALS),
      pressure: cleanPressure(pts[i].pressure),
    };
    const last = out[out.length - 1];
    if (last) {
      const far = Math.hypot(p.x - last.x, p.y - last.y) >= minDistance;
      // Keep the final point regardless of distance (it defines where the
      // stroke ends) unless rounding made it an exact duplicate.
      const isLast = i === pts.length - 1;
      if (!far && !isLast) continue;
      if (!far && p.x === last.x && p.y === last.y) continue;
    }
    out.push(p);
  }
  return { ...stroke, points: out };
}

export function serializeStrokes(strokes: Stroke[]): string {
  return JSON.stringify(strokes);
}

/** Parse strokes JSON defensively — returns [] for anything malformed so a
 * corrupt row can never crash the editor. */
export function parseStrokes(json: string | null | undefined): Stroke[] {
  if (!json) return [];
  let raw: unknown;
  try {
    raw = JSON.parse(json);
  } catch {
    return [];
  }
  if (!Array.isArray(raw)) return [];
  const out: Stroke[] = [];
  for (const s of raw) {
    if (!s || typeof s !== 'object') continue;
    const points = (s as { points?: unknown }).points;
    if (!Array.isArray(points)) continue;
    const clean: StrokePoint[] = [];
    for (const p of points) {
      if (!p || typeof p !== 'object') continue;
      const { x, y, pressure } = p as Record<string, unknown>;
      if (typeof x === 'number' && typeof y === 'number') {
        clean.push({
          x,
          y,
          pressure: typeof pressure === 'number' ? pressure : 0.5,
        });
      }
    }
    if (clean.length > 0) {
      const rawTool = (s as { tool?: unknown }).tool;
      const tool: StrokeTool =
        rawTool === 'highlighter' || rawTool === 'eraser' ? rawTool : 'pen';
      const rawSize = (s as { size?: unknown }).size;
      const size =
        typeof rawSize === 'number' && rawSize > 0
          ? rawSize
          : DEFAULT_STROKE_SIZE;
      out.push({ tool, size, points: clean });
    }
  }
  return out;
}

export type SwipeDirection = 'next' | 'prev';

export interface SwipeResult {
  /** The page index to move to. */
  index: number;
  /** True when moving past the last page: the caller must create a new page,
   * which will land at this index. */
  createPage: boolean;
}

/** Resolve a horizontal swipe into a target page. Swiping `next` past the last
 * page signals that a new page should be created (createPage). Swiping `prev`
 * at the first page is a no-op. */
export function resolveSwipe(
  direction: SwipeDirection,
  currentIndex: number,
  pageCount: number
): SwipeResult {
  if (direction === 'next') {
    if (currentIndex < pageCount - 1) {
      return { index: currentIndex + 1, createPage: false };
    }
    return { index: currentIndex + 1, createPage: true };
  }
  if (currentIndex > 0) return { index: currentIndex - 1, createPage: false };
  return { index: 0, createPage: false };
}

/** Whether a pointer displacement is a decisive horizontal swipe (used to flip
 * pages with a finger). Must be mostly horizontal and clear the threshold. */
export function isHorizontalSwipe(
  dx: number,
  dy: number,
  threshold = 60
): boolean {
  return Math.abs(dx) >= threshold && Math.abs(dx) > Math.abs(dy);
}

/** Whether a pointer displacement + duration reads as a deliberate tap rather
 * than a drag. Used for the two-finger-tap eraser toggle: Apple Pencil's
 * double-tap is not exposed to any browser, so an on-canvas gesture is the only
 * way to switch tools without reaching for the toolbar. */
export function isTap(
  dx: number,
  dy: number,
  durationMs: number,
  maxMove = 14,
  maxMs = 320
): boolean {
  return Math.hypot(dx, dy) <= maxMove && durationMs <= maxMs;
}

/** Map pen pressure to a stroke width. Pressure 0 still leaves a visible line;
 * full pressure reaches `base`. Mouse events (no pressure) report 0.5. */
export function strokeWidth(base: number, pressure: number): number {
  const p = Number.isFinite(pressure)
    ? Math.min(Math.max(pressure, 0), 1)
    : 0.5;
  return base * (0.35 + 0.65 * p);
}
