// The ink model: pure, space-agnostic stroke logic shared by every drawing
// surface in the app (the Paper editor, the Study desk's embedded page, and the
// newspaper reader's markup layer).
//
// Nothing here knows how big a page is or what units its coordinates are in.
// That is deliberate: Paper works in a fixed A4 space (tenths of a millimetre)
// while the newspaper works in a per-page space derived from the PDF's aspect,
// and both need the same undo model, the same eraser and the same
// simplification. Page-specific geometry lives beside the surface that owns it
// — see src/lib/paper.ts for the A4 half.
//
// Kept out of any component so it can be unit-tested without a real 2D context
// (jsdom has none). See src/lib/ink.test.ts and src/lib/paper.test.ts.

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
  /** Base stroke width in the surface's own units (pen pressure modulates
   * around it). */
  size: number;
  points: StrokePoint[];
  /** Absent means "whatever this surface calls ink", which is why it is
   * optional rather than defaulted at parse time: every stroke written before
   * there was a colour picker has none, and Paper's default is black while the
   * newspaper's is blue. Resolving it against the surface's palette at paint
   * time is what lets both histories keep the colour they were drawn in. */
  color?: string;
}

/** What a surface calls ink when a stroke does not say. */
export interface InkPalette {
  ink: string;
  highlight: string;
  highlightAlpha: number;
}

/** The colour a stroke is actually painted in. */
export function strokeColor(stroke: Stroke, palette: InkPalette): string {
  if (stroke.color) return stroke.color;
  return stroke.tool === 'highlighter' ? palette.highlight : palette.ink;
}

/** Offered in the tool panel. Deliberately short: these are picked with a
 * stylus on a tablet, and a fifth swatch costs a row. */
export const PEN_COLORS: readonly string[] = [
  '#111111',
  '#1756ad',
  '#c0392b',
  '#1e8449',
];

export const HIGHLIGHTER_COLORS: readonly string[] = [
  '#ffe14d',
  '#7dffb0',
  '#ff9ad5',
  '#8fd3ff',
];

/** The swatches a tool offers, or none for one that has no colour (the eraser
 * removes ink rather than laying any down). */
export function colorsFor(tool: StrokeTool): readonly string[] {
  if (tool === 'pen') return PEN_COLORS;
  if (tool === 'highlighter') return HIGHLIGHTER_COLORS;
  return [];
}

/** Fallback width when a stored stroke has a missing/invalid size. */
export const DEFAULT_STROKE_SIZE = 8;

export interface Size {
  width: number;
  height: number;
}

/** Diameter, in CSS pixels, of the dot that previews a stroke width in the tool
 * panel. Anything past 18px stops fitting the button.
 *
 * `unitsPerPx` is the surface's own scale: an A4 page unit is about half a CSS
 * pixel (the default, which is what Paper has always used), while the
 * newspaper's units are thousandths of a page width and a size-2 pen would
 * otherwise preview as a 1px speck. `minPx` keeps the smallest width visible
 * as a dot rather than a dust mote. */
export function sizeDotPx(size: number, unitsPerPx = 2, minPx = 0): number {
  return Math.min(Math.max(size / unitsPerPx, minPx), 18);
}

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

/** Maximum span of a dense run considered for point reduction, in page units.
 * Corners and pressure changes can retain closer points. Two A4 page units
 * are a fifth of a millimetre. */
export const MIN_POINT_DISTANCE = 2;

/** Decimal places kept for coordinates (a tenth of a page unit is a hundredth
 * of a millimetre, well below what any display can resolve) and for pressure. */
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

/** Compact short runs only when every removed point stays within 0.1 units
 * of the replacement segment and its pressure within 0.01 of interpolation.
 * A bounded window keeps this linear even for very dense input. The same
 * processing is used in the live preview and on commit. */
export function simplifyStroke(
  stroke: Stroke,
  minDistance = MIN_POINT_DISTANCE
): Stroke {
  const pts: StrokePoint[] = [];
  for (const point of stroke.points) {
    const p = {
      x: roundTo(point.x, COORD_DECIMALS),
      y: roundTo(point.y, COORD_DECIMALS),
      pressure: cleanPressure(point.pressure),
    };
    const prev = pts[pts.length - 1];
    if (
      prev &&
      p.x === prev.x &&
      p.y === prev.y &&
      p.pressure === prev.pressure
    )
      continue;
    pts.push(p);
  }
  if (pts.length < 3) return { ...stroke, points: pts };
  const out = [pts[0]];
  let anchor = 0;
  for (let end = 2; end < pts.length; end++) {
    const a = pts[anchor],
      b = pts[end];
    const dx = b.x - a.x,
      dy = b.y - a.y;
    const length2 = dx * dx + dy * dy;
    let fits = end - anchor <= 32 && length2 > 0 && length2 <= minDistance ** 2;
    let previousT = 0;
    for (let i = anchor + 1; fits && i < end; i++) {
      const p = pts[i];
      const t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / length2;
      // Monotonic projection preserves turnarounds, even along a straight line.
      fits =
        t >= previousT &&
        t <= 1 &&
        Math.hypot(p.x - a.x - t * dx, p.y - a.y - t * dy) <= 0.1 &&
        Math.abs(p.pressure - (a.pressure + t * (b.pressure - a.pressure))) <=
          0.01;
      previousT = t;
    }
    if (!fits) {
      out.push(pts[end - 1]);
      anchor = end - 1;
    }
  }
  out.push(pts[pts.length - 1]);
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
  return parseStrokeArray(raw);
}

/** The already-decoded half of `parseStrokes`, so a stroke array nested inside
 * another JSON document doesn't have to be re-stringified to be validated. */
export function parseStrokeArray(raw: unknown): Stroke[] {
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
      const rawColor = (s as { color?: unknown }).color;
      const color = typeof rawColor === 'string' ? rawColor : undefined;
      out.push(
        color
          ? { tool, size, points: clean, color }
          : { tool, size, points: clean }
      );
    }
  }
  return out;
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
