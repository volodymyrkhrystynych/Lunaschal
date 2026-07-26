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

/** Undo/redo model: the committed strokes plus the redo stack. */
export interface StrokeState {
  strokes: Stroke[];
  redo: Stroke[];
}

export const emptyStrokeState = (): StrokeState => ({ strokes: [], redo: [] });

/** Commit a finished stroke. Any pending redo history is discarded, matching
 * the usual undo/redo contract (a new edit forks the timeline). */
export function commitStroke(state: StrokeState, stroke: Stroke): StrokeState {
  return { strokes: [...state.strokes, stroke], redo: [] };
}

export function undo(state: StrokeState): StrokeState {
  if (state.strokes.length === 0) return state;
  const last = state.strokes[state.strokes.length - 1];
  return { strokes: state.strokes.slice(0, -1), redo: [...state.redo, last] };
}

export function redo(state: StrokeState): StrokeState {
  if (state.redo.length === 0) return state;
  const last = state.redo[state.redo.length - 1];
  return { strokes: [...state.strokes, last], redo: state.redo.slice(0, -1) };
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

/** Map pen pressure to a stroke width. Pressure 0 still leaves a visible line;
 * full pressure reaches `base`. Mouse events (no pressure) report 0.5. */
export function strokeWidth(base: number, pressure: number): number {
  const p = Number.isFinite(pressure)
    ? Math.min(Math.max(pressure, 0), 1)
    : 0.5;
  return base * (0.35 + 0.65 * p);
}
