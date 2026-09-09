// Geometry for the floating tool panel that every drawing surface shares: the
// edge it snaps to, where along that edge it sits, and where that lands in CSS
// pixels inside whatever area it floats over.
//
// Split out from src/lib/paper.ts because the panel is not about an A4 page —
// the newspaper reader mounts the same panel over a scrolling column of PDF
// pages, and a newspaper component importing `@/lib/paper` would be exactly the
// coupling this refactor exists to remove.

import type { Size } from './ink';

const positive = (n: number): number => (Number.isFinite(n) && n > 0 ? n : 0);

const clamp01 = (n: number): number =>
  Number.isFinite(n) ? Math.min(Math.max(n, 0), 1) : 0.5;

const clamp = (n: number, lo: number, hi: number): number =>
  Math.min(Math.max(n, lo), hi < lo ? lo : hi);

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
export function loadPanelPlacement(
  key: string = PANEL_PLACEMENT_KEY
): PanelPlacement {
  try {
    return (
      parsePanelPlacement(localStorage.getItem(key)) ?? DEFAULT_PANEL_PLACEMENT
    );
  } catch {
    return DEFAULT_PANEL_PLACEMENT;
  }
}

export function savePanelPlacement(
  placement: PanelPlacement,
  key: string = PANEL_PLACEMENT_KEY
): void {
  try {
    localStorage.setItem(key, JSON.stringify(placement));
  } catch {
    /* storage unavailable — the panel just forgets where it was */
  }
}
