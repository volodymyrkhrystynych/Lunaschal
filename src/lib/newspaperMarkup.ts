// The newspaper reader's markup model: what is stored for an issue, how it maps
// onto the shared ink model, and an undo history that is page-correct.
//
// Two coordinate spaces meet here, and the split is the point:
//
//  - **Wire/model space** — points normalised to 0..1 of their own page. This
//    is what the server stores and what this module holds, because it is the
//    only space that means anything before a page has been rendered and its
//    aspect ratio is known.
//  - **Ink space** — `{1000, 1000 * ratio}` for a page of that aspect, which is
//    what the shared ink model draws and erases in. A single uniform scale
//    reaches the screen from here, exactly as the reader's SVG viewBox always
//    did.
//
// Geometry belongs to the page, history belongs to the issue. A `Page` knows
// its own ratio and converts; this module never needs one. That is not just
// tidiness — the eraser is a circle of a given radius, and running it in
// normalised space (where x and y scale differently on a page that is taller
// than it is wide) would quietly make it an ellipse.
//
// See src/lib/newspaperMarkup.test.ts.

import {
  commitStroke,
  emptyStrokeState,
  redo as redoState,
  undo as undoState,
  type Size,
  type Stroke,
  type StrokeState,
} from './ink';

/** A stored point: x, y, and optionally the pen pressure it was drawn with.
 * Strokes written before there was any pressure are plain pairs. */
export type WirePoint = [number, number] | [number, number, number];

export interface WireStroke {
  page: number;
  tool: string;
  points: WirePoint[];
  size?: number;
  color?: string;
}

/** Ink units are thousandths of a page's width, so these are literally the
 * widths the reader has always drawn: `strokeWidth={2}` and `{16}`. */
export const NEWSPAPER_TOOL_SIZES = {
  pen: [2, 3, 4.5],
  highlighter: [10, 16, 24],
  eraser: [12, 24, 40],
} as const;

/** What the reader has always drawn in, and so what a stroke with no colour of
 * its own must still come back as. */
export const NEWSPAPER_PALETTE = {
  ink: '#1756ad',
  highlight: '#ffdb00',
  highlightAlpha: 0.35,
};

const DEFAULT_SIZE: Record<string, number> = {
  pen: 2,
  highlighter: 16,
  eraser: 24,
};

/** Server-side budgets, mirrored here so the reader can refuse before it sends
 * (backend/newspapers/issues.py). */
export const MAX_STROKES = 10000;
export const MAX_POINTS_PER_STROKE = 10000;
export const MAX_POINTS = 100000;

export const inkSpaceFor = (ratio: number): Size => ({
  width: 1000,
  height: 1000 * ratio,
});

// --- wire <-> ink -----------------------------------------------------------

/** Lift a stored stroke into the ink space of a page with this aspect ratio.
 *
 * A point with no stored pressure becomes 1, not the 0.5 a mouse reports. That
 * is what keeps existing markup pixel-identical: the reader used to draw every
 * stroke at a flat `size`, and `strokeWidth(size, 1)` is exactly `size`, while
 * 0.5 would quietly thin every mark ever made by a third. */
export function toInkStroke(stroke: Stroke, ratio: number): Stroke {
  return {
    ...stroke,
    points: stroke.points.map(p => ({
      x: p.x * 1000,
      y: p.y * 1000 * ratio,
      pressure: p.pressure,
    })),
  };
}

/** Drop a freshly drawn stroke back into the stored space.
 *
 * The ratio cancels: a point captured as `(clientY - top) / height * 1000 *
 * ratio` divides straight back out. So a page still holding the default aspect
 * because pdf.js has not reported its real one yet cannot misplace a stored
 * point — only the shape of the box on screen is provisional, never the ink. */
export function toWireStroke(stroke: Stroke, ratio: number): Stroke {
  return {
    ...stroke,
    points: stroke.points.map(p => ({
      x: p.x / 1000,
      y: ratio === 0 ? 0 : p.y / (1000 * ratio),
      pressure: p.pressure,
    })),
  };
}

const clamp01 = (n: number): number => Math.min(Math.max(n, 0), 1);

/** Decode one stored stroke. Defensive throughout: a malformed row must render
 * as less markup, never as a crash. */
export function parseWireStroke(
  raw: unknown
): { page: number; stroke: Stroke } | null {
  if (!raw || typeof raw !== 'object') return null;
  const { page, tool, points, size, color } = raw as Record<string, unknown>;
  if (typeof page !== 'number' || !Number.isInteger(page) || page < 1) {
    return null;
  }
  if (!Array.isArray(points)) return null;
  // 'highlight' is what the column has always held; 'highlighter' is what the
  // shared ink model calls it.
  const inkTool =
    tool === 'highlight' || tool === 'highlighter' ? 'highlighter' : 'pen';
  const clean = [];
  for (const p of points) {
    if (!Array.isArray(p) || p.length < 2) continue;
    const [x, y, pressure] = p;
    if (typeof x !== 'number' || typeof y !== 'number') continue;
    if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
    clean.push({
      x: clamp01(x),
      y: clamp01(y),
      pressure:
        typeof pressure === 'number' && Number.isFinite(pressure)
          ? clamp01(pressure)
          : 1,
    });
  }
  if (!clean.length) return null;
  return {
    page,
    stroke: {
      tool: inkTool,
      size:
        typeof size === 'number' && Number.isFinite(size) && size > 0
          ? size
          : DEFAULT_SIZE[inkTool],
      points: clean,
      ...(typeof color === 'string' && color ? { color } : {}),
    },
  };
}

const round = (n: number, places: number): number => {
  const f = 10 ** places;
  return Math.round(n * f) / f;
};

/** Encode one stroke for storage. Coordinates keep four decimals — a
 * ten-thousandth of a page width is far below anything a screen can show, and
 * full float precision is pure payload against a 100 000-point budget. */
export function serializeWireStroke(stroke: Stroke, page: number): WireStroke {
  return {
    page,
    tool: stroke.tool === 'highlighter' ? 'highlight' : 'pen',
    points: stroke.points.map(p => [
      round(p.x, 4),
      round(p.y, 4),
      round(p.pressure, 2),
    ]),
    size: round(stroke.size, 2),
    ...(stroke.color ? { color: stroke.color } : {}),
  };
}

// --- the issue's markup -----------------------------------------------------

export interface IssueMarkup {
  /** One undo/redo history per page. Erasing is a page-local act, and so is
   * the snapshot it takes. */
  pages: Map<number, StrokeState>;
  /** Which page each edit landed on, oldest first, so Undo can reach the last
   * edit wherever it happened. */
  stack: number[];
  redoStack: number[];
}

export const emptyMarkup = (): IssueMarkup => ({
  pages: new Map(),
  stack: [],
  redoStack: [],
});

export const strokesOn = (markup: IssueMarkup, page: number): Stroke[] =>
  markup.pages.get(page)?.strokes ?? [];

export const canUndo = (markup: IssueMarkup): boolean =>
  markup.stack.length > 0;
export const canRedo = (markup: IssueMarkup): boolean =>
  markup.redoStack.length > 0;

export function fromWire(raw: unknown): IssueMarkup {
  const markup = emptyMarkup();
  if (!Array.isArray(raw)) return markup;
  for (const entry of raw) {
    const decoded = parseWireStroke(entry);
    if (!decoded) continue;
    const state = markup.pages.get(decoded.page) ?? emptyStrokeState();
    // No history: loaded markup is not this session's to undo. Seeding one
    // snapshot per stroke would be quadratic against a 10 000-stroke cap, and
    // the eraser is the better answer for ink from an earlier sitting anyway.
    markup.pages.set(decoded.page, {
      ...state,
      strokes: [...state.strokes, decoded.stroke],
    });
  }
  return markup;
}

/** Stored order: by page, then by the order the strokes were laid down, which
 * is also the order they must be painted in. */
export function toWire(markup: IssueMarkup): WireStroke[] {
  const out: WireStroke[] = [];
  for (const page of [...markup.pages.keys()].sort((a, b) => a - b)) {
    for (const stroke of strokesOn(markup, page)) {
      out.push(serializeWireStroke(stroke, page));
    }
  }
  return out;
}

export const countStrokes = (markup: IssueMarkup): number =>
  [...markup.pages.values()].reduce((n, s) => n + s.strokes.length, 0);

export const countPoints = (markup: IssueMarkup): number =>
  [...markup.pages.values()].reduce(
    (n, s) => n + s.strokes.reduce((m, k) => m + k.points.length, 0),
    0
  );

/** A new edit forks the timeline, so every page's redo is discarded — not just
 * the edited page's.
 *
 * Clearing only the edited page would leave the global redo stack naming a page
 * whose own redo list had moved on, and Redo would then resurrect a stroke from
 * a different edit entirely, out of order and on a page you were not looking
 * at. */
function withEdit(
  markup: IssueMarkup,
  page: number,
  next: StrokeState
): IssueMarkup {
  const pages = new Map(markup.pages);
  pages.set(page, next);
  for (const [key, state] of pages) {
    if (state.redo.length) pages.set(key, { ...state, redo: [] });
  }
  return { pages, stack: [...markup.stack, page], redoStack: [] };
}

/** Replace a page's strokes, recording one undoable step.
 *
 * Erasing is expressed this way rather than as its own operation: the eraser's
 * geometry belongs to the page that knows its aspect ratio, so by the time it
 * reaches here it has already become "these are the strokes that survived". */
export function setStrokesOn(
  markup: IssueMarkup,
  page: number,
  strokes: Stroke[]
): IssueMarkup {
  const state = markup.pages.get(page) ?? emptyStrokeState();
  return withEdit(markup, page, {
    strokes,
    history: [...state.history, state.strokes],
    redo: [],
  });
}

export function commitOn(
  markup: IssueMarkup,
  page: number,
  stroke: Stroke
): IssueMarkup {
  const state = markup.pages.get(page) ?? emptyStrokeState();
  return withEdit(markup, page, commitStroke(state, stroke));
}

export function undoLast(markup: IssueMarkup): IssueMarkup {
  const page = markup.stack[markup.stack.length - 1];
  if (page === undefined) return markup;
  const state = markup.pages.get(page);
  if (!state) return markup;
  const pages = new Map(markup.pages);
  pages.set(page, undoState(state));
  return {
    pages,
    stack: markup.stack.slice(0, -1),
    redoStack: [...markup.redoStack, page],
  };
}

export function redoLast(markup: IssueMarkup): IssueMarkup {
  const page = markup.redoStack[markup.redoStack.length - 1];
  if (page === undefined) return markup;
  const state = markup.pages.get(page);
  if (!state) return markup;
  const pages = new Map(markup.pages);
  pages.set(page, redoState(state));
  return {
    pages,
    stack: [...markup.stack, page],
    redoStack: markup.redoStack.slice(0, -1),
  };
}
