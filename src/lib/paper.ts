// Pure, node-testable logic for the Paper drawing view: everything that is
// specific to an A4 sheet — its coordinate space, the contain-fit that puts it
// on screen, the local buffer codec and the floating tool panel's placement.
//
// The stroke model itself (strokes, undo/redo, the eraser, simplification,
// pressure) lives in src/lib/ink.ts and is shared with the newspaper reader's
// markup layer. It is re-exported here so the Paper components can keep
// importing everything they need from one place.
//
// See src/lib/paper.test.ts.

import {
  parseStrokeArray,
  type Size,
  type Stroke,
  type StrokeTool,
} from './ink';

export * from './ink';

/** A page is always A4 portrait, and every stored coordinate lives in this fixed
 * space — units are tenths of a millimetre, so 2100 × 2970 is 210 mm × 297 mm.
 *
 * This is the whole point of the page-space model: ink used to be stored in the
 * canvas's CSS pixels at the moment it was drawn, so a page written on a wide
 * desktop window came back stretched on an iPad held in portrait (the renderer
 * scaled x and y by different factors). Page space is resolution-independent
 * and ratio-stable: the on-screen box always has this exact aspect, so the
 * logical→screen transform is a single uniform scale. */
export const PAGE_WIDTH = 2100;
export const PAGE_HEIGHT = 2970;
export const PAGE_ASPECT = PAGE_WIDTH / PAGE_HEIGHT;

/** Selectable widths per tool (Small / Medium / Large), in page units. Roughly
 * 2 page units per CSS pixel at the size a page renders on a laptop. */
export const TOOL_SIZES: Record<StrokeTool, readonly number[]> = {
  pen: [4, 8, 14],
  highlighter: [28, 44, 68],
  eraser: [32, 60, 100],
};

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

// --- Page geometry ---------------------------------------------------------

export interface PageBox {
  /** On-screen size of the page, in CSS pixels. Always in PAGE_ASPECT. */
  width: number;
  height: number;
  /** Where the page sits inside the viewport, in CSS pixels (centred, so the
   * leftover shows as bars on two sides). */
  left: number;
  top: number;
  /** page units -> CSS pixels. One factor for both axes, by construction. */
  scale: number;
}

const positive = (n: number): number => (Number.isFinite(n) && n > 0 ? n : 0);

/** Fit an A4 page into the available area, contain-style: as large as it goes
 * without cropping, centred, leaving bars on the two sides the viewport has to
 * spare. The page never adopts the viewport's own aspect ratio — that is what
 * used to distort saved ink. */
export function fitPageBox(viewport: Size, aspect = PAGE_ASPECT): PageBox {
  const vw = positive(viewport.width);
  const vh = positive(viewport.height);
  if (vw === 0 || vh === 0) {
    return { width: 0, height: 0, left: 0, top: 0, scale: 0 };
  }
  let width = vw;
  let height = vw / aspect;
  if (height > vh) {
    height = vh;
    width = vh * aspect;
  }
  return {
    width,
    height,
    left: (vw - width) / 2,
    top: (vh - height) / 2,
    scale: width / PAGE_WIDTH,
  };
}

/** Map a pointer position expressed relative to the page's top-left corner (CSS
 * pixels) into page space. The inverse is `x * box.width / PAGE_WIDTH`. */
export function toPageSpace(
  offsetX: number,
  offsetY: number,
  box: Size
): { x: number; y: number } {
  const w = positive(box.width);
  const h = positive(box.height);
  if (w === 0 || h === 0) return { x: 0, y: 0 };
  return { x: (offsetX / w) * PAGE_WIDTH, y: (offsetY / h) * PAGE_HEIGHT };
}

/** Whether a stored page size is already the fixed page space (or absent, which
 * only happens for a page that was never saved). */
export function isPageSpace(size: Size | null | undefined): boolean {
  if (!size || !size.width || !size.height) return true;
  return size.width === PAGE_WIDTH && size.height === PAGE_HEIGHT;
}

/** Convert strokes stored in an older page's screen-sized coordinate space into
 * page space.
 *
 * Pages saved before the A4 page space kept their points in whatever CSS-pixel
 * box the canvas happened to have when the ink was laid down, recorded on the
 * row as width/height. They are converted on read (never rewritten in place —
 * the row keeps its old strokes and old size until the next save writes both
 * together), using a uniform contain fit so the drawing keeps its shape and is
 * centred on the page instead of being stretched to A4. */
export function toPageSpaceStrokes(
  strokes: Stroke[],
  source: Size | null | undefined
): Stroke[] {
  if (isPageSpace(source)) return strokes;
  const sw = positive(source!.width);
  const sh = positive(source!.height);
  if (sw === 0 || sh === 0) return strokes;
  const scale = Math.min(PAGE_WIDTH / sw, PAGE_HEIGHT / sh);
  const dx = (PAGE_WIDTH - sw * scale) / 2;
  const dy = (PAGE_HEIGHT - sh * scale) / 2;
  return strokes.map(s => ({
    ...s,
    size: s.size * scale,
    points: s.points.map(p => ({
      x: p.x * scale + dx,
      y: p.y * scale + dy,
      pressure: p.pressure,
    })),
  }));
}

// --- Local (IndexedDB) buffer ----------------------------------------------

/** Marks a buffer as holding page-space coordinates. A buffer written by an
 * older build is a bare stroke array with no marker, which is how the two are
 * told apart — unsaved ink from before the change must not be thrown away. */
const BUFFER_SPACE = 'a4';

export function serializeBuffer(strokes: Stroke[]): string {
  return JSON.stringify({ space: BUFFER_SPACE, strokes });
}

/** Read a local buffer back. Returns null when there is no usable buffer (so
 * "empty page held locally" stays distinct from "nothing held locally").
 * `legacySize` is the page's stored size, used to convert a pre-page-space
 * buffer the same way the stored strokes are converted. */
export function parseBuffer(
  raw: unknown,
  legacySize: Size | null | undefined
): Stroke[] | null {
  if (typeof raw !== 'string' || !raw) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (Array.isArray(parsed)) {
    return toPageSpaceStrokes(parseStrokeArray(parsed), legacySize);
  }
  if (
    parsed &&
    typeof parsed === 'object' &&
    (parsed as { space?: unknown }).space === BUFFER_SPACE
  ) {
    return parseStrokeArray((parsed as { strokes?: unknown }).strokes);
  }
  return null;
}

// --- Floating tool panel ---------------------------------------------------

export type SnapEdge = 'top' | 'right' | 'bottom' | 'left';

export const SNAP_EDGES: readonly SnapEdge[] = [
  'top',
  'right',
  'bottom',
  'left',
];

export interface PanelPlacement {
  edge: SnapEdge;
  /** Where along that edge the panel sits, 0..1 (left→right or top→bottom). */
  offset: number;
}

/** Docked left by default: it keeps the top of the page — where the hand
 * naturally rests when writing — clear. */
export const DEFAULT_PANEL_PLACEMENT: PanelPlacement = {
  edge: 'left',
  offset: 0.5,
};

const clamp01 = (n: number): number =>
  Number.isFinite(n) ? Math.min(Math.max(n, 0), 1) : 0.5;

const clamp = (n: number, lo: number, hi: number): number =>
  Math.min(Math.max(n, lo), hi < lo ? lo : hi);

/** The edge a dropped panel belongs to: whichever one its centre is nearest.
 * The centre is clamped into the area first — a drag that ends past the edge
 * (or off the screen entirely) still resolves to the edge it was heading for. */
export function resolveSnapEdge(
  point: { x: number; y: number },
  bounds: Size
): SnapEdge {
  const w = positive(bounds.width);
  const h = positive(bounds.height);
  const x = clamp(point.x, 0, w);
  const y = clamp(point.y, 0, h);
  const distances: Record<SnapEdge, number> = {
    top: y,
    bottom: h - y,
    left: x,
    right: w - x,
  };
  let best: SnapEdge = 'top';
  for (const edge of SNAP_EDGES) {
    if (distances[edge] < distances[best]) best = edge;
  }
  return best;
}

/** Resolve a drop into a placement: nearest edge plus how far along it the
 * panel was left, kept as a fraction so a rotation or window resize puts it
 * back in the same relative spot. */
export function snapPlacement(
  centre: { x: number; y: number },
  bounds: Size
): PanelPlacement {
  const edge = resolveSnapEdge(centre, bounds);
  const w = positive(bounds.width);
  const h = positive(bounds.height);
  const offset =
    edge === 'top' || edge === 'bottom'
      ? w === 0
        ? 0.5
        : centre.x / w
      : h === 0
        ? 0.5
        : centre.y / h;
  return { edge, offset: clamp01(offset) };
}

export function panelOrientation(edge: SnapEdge): 'horizontal' | 'vertical' {
  return edge === 'top' || edge === 'bottom' ? 'horizontal' : 'vertical';
}

/** Absolute position of the docked panel inside the drawing area, clamped so it
 * always stays fully on screen. */
export function panelPosition(
  placement: PanelPlacement,
  bounds: Size,
  panel: Size,
  margin = 12
): { left: number; top: number } {
  const w = positive(bounds.width);
  const h = positive(bounds.height);
  const pw = positive(panel.width);
  const ph = positive(panel.height);
  const maxLeft = w - pw - margin;
  const maxTop = h - ph - margin;
  const alongX = clamp(clamp01(placement.offset) * w - pw / 2, margin, maxLeft);
  const alongY = clamp(clamp01(placement.offset) * h - ph / 2, margin, maxTop);
  switch (placement.edge) {
    case 'top':
      return { left: alongX, top: margin };
    case 'bottom':
      return { left: alongX, top: clamp(maxTop, margin, maxTop) };
    case 'left':
      return { left: margin, top: alongY };
    case 'right':
      return { left: clamp(maxLeft, margin, maxLeft), top: alongY };
  }
}

export const PANEL_PLACEMENT_KEY = 'lunaschal:paperToolPanel';

export function parsePanelPlacement(
  raw: string | null | undefined
): PanelPlacement | null {
  if (!raw) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== 'object') return null;
  const { edge, offset } = parsed as { edge?: unknown; offset?: unknown };
  if (!SNAP_EDGES.includes(edge as SnapEdge)) return null;
  return {
    edge: edge as SnapEdge,
    offset: typeof offset === 'number' ? clamp01(offset) : 0.5,
  };
}

/** Where the panel was left last time. Persisted the same way the workout draft
 * and the current view are (localStorage), and equally best-effort: a private
 * window that throws on access just gets the default. */
export function loadPanelPlacement(): PanelPlacement {
  try {
    return (
      parsePanelPlacement(localStorage.getItem(PANEL_PLACEMENT_KEY)) ??
      DEFAULT_PANEL_PLACEMENT
    );
  } catch {
    return DEFAULT_PANEL_PLACEMENT;
  }
}

export function savePanelPlacement(placement: PanelPlacement): void {
  try {
    localStorage.setItem(PANEL_PLACEMENT_KEY, JSON.stringify(placement));
  } catch {
    /* storage unavailable — the panel just forgets where it was */
  }
}

/** The save indicator's text. It lives in a fixed-width slot in the top bar and
 * deliberately *not* in the tool panel: when it shared a row with the tools, it
 * popped in and out on every save and reflowed the whole bar, which made drawing
 * near the top edge a game of chase.
 *
 * Counts *pages*, not strokes, because Save sends the whole paper: a page
 * written on and flipped away from is still unsaved, and a status that only knew
 * about the page on screen said "Saved" while three others were waiting. */
export function saveStatusLabel(saving: boolean, unsavedPages: number): string {
  if (saving) return 'Saving…';
  if (unsavedPages > 1) return `${unsavedPages} unsaved`;
  if (unsavedPages === 1) return 'Unsaved';
  return 'Saved';
}
