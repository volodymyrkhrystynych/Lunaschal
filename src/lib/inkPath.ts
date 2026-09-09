// Turning a stroke into SVG path geometry.
//
// A `<polyline>` carries one stroke-width for its whole length, which is why an
// SVG surface cannot show pen pressure that way — and why the newspaper reader
// had no pressure at all while it drew polylines. A tapering stroke has to be
// drawn as the *outline* of a variable-width ribbon and filled: one `<path>`
// per stroke, sharp at any zoom, and one element rather than one per segment
// (a stroke crossing an A4 page simplifies to ~500 points, so per-segment
// elements would put six figures of them on a densely written page).
//
// Pure and node-testable. See src/lib/inkPath.test.ts.

import { strokeWidth, type Stroke } from './ink';

export interface Vec {
  x: number;
  y: number;
}

const EPS = 1e-9;
/** How far a join may be widened to keep a sharp corner from pinching. Averaging
 * the two segment normals shortens the offset by cos(θ/2); dividing it back out
 * restores the width, but goes to infinity as the corner doubles back, so it is
 * capped the way a miter limit is. */
const MAX_JOIN_STRETCH = 2;

/** Half-width at each point. Only the pen tapers — the highlighter is a flat
 * band, and the eraser is never drawn at all. */
export function strokeRadii(stroke: Stroke): number[] {
  const taper = stroke.tool === 'pen';
  return stroke.points.map(
    p => (taper ? strokeWidth(stroke.size, p.pressure) : stroke.size) / 2
  );
}

/** Unit normal of each segment, with degenerate (zero-length) segments taking
 * the nearest real one so a repeated point cannot punch a hole in the ribbon. */
function segmentNormals(stroke: Stroke): (Vec | null)[] {
  const pts = stroke.points;
  const seg: (Vec | null)[] = [];
  for (let i = 0; i < pts.length - 1; i++) {
    const dx = pts[i + 1].x - pts[i].x;
    const dy = pts[i + 1].y - pts[i].y;
    const len = Math.hypot(dx, dy);
    seg.push(len < EPS ? null : { x: -dy / len, y: dx / len });
  }
  let carry: Vec | null = null;
  for (let i = 0; i < seg.length; i++) {
    if (seg[i]) carry = seg[i];
    else seg[i] = carry;
  }
  carry = null;
  for (let i = seg.length - 1; i >= 0; i--) {
    if (seg[i]) carry = seg[i];
    else seg[i] = carry;
  }
  return seg;
}

/** The two sides of the ribbon: one offset point per stroke point, per side.
 *
 * Returns null when the stroke has no length at all — a tap, or a run of
 * identical points — which is drawn as a dot instead. */
export function strokeOutline(
  stroke: Stroke
): { left: Vec[]; right: Vec[] } | null {
  const pts = stroke.points;
  if (pts.length < 2) return null;
  const seg = segmentNormals(stroke);
  if (!seg.some(Boolean)) return null;
  const radii = strokeRadii(stroke);

  const left: Vec[] = [];
  const right: Vec[] = [];
  for (let i = 0; i < pts.length; i++) {
    const before = i > 0 ? seg[i - 1] : null;
    const after = i < seg.length ? seg[i] : null;
    let n: Vec;
    let stretch = 1;
    if (before && after) {
      const x = before.x + after.x;
      const y = before.y + after.y;
      const len = Math.hypot(x, y);
      if (len > EPS) {
        n = { x: x / len, y: y / len };
        // cos(θ/2) between the averaged normal and either segment's own.
        const cos = n.x * after.x + n.y * after.y;
        stretch = cos > EPS ? Math.min(1 / cos, MAX_JOIN_STRETCH) : 1;
      } else {
        // The stroke doubles straight back on itself; there is no meaningful
        // average, so keep the outgoing side square.
        n = after;
      }
    } else {
      n = (before ?? after)!;
    }
    const r = radii[i] * stretch;
    left.push({ x: pts[i].x + n.x * r, y: pts[i].y + n.y * r });
    right.push({ x: pts[i].x - n.x * r, y: pts[i].y - n.y * r });
  }
  return { left, right };
}

/** Coordinates are rounded to a hundredth of a space unit: far below anything a
 * display resolves, and it keeps the `d` attribute short enough to rebuild on
 * every frame of a stroke in flight. */
const f = (n: number): string => String(Math.round(n * 100) / 100);

/** A filled circle, for a stroke with no length — a tap of the pen. */
export function dotPathData(x: number, y: number, r: number): string {
  if (!(r > 0)) return '';
  return (
    `M${f(x - r)} ${f(y)}` +
    `A${f(r)} ${f(r)} 0 1 0 ${f(x + r)} ${f(y)}` +
    `A${f(r)} ${f(r)} 0 1 0 ${f(x - r)} ${f(y)}Z`
  );
}

/** The whole stroke as one closed, fillable path: up the left side, a round cap
 * over the tip, back down the right side, and a round cap over the start.
 *
 * Both caps sweep the same way (flag 0). The normal is the segment direction
 * rotated a quarter turn, so travelling left → tip → right walks the circle in
 * the direction of *decreasing* angle, which is what flag 0 means. */
export function strokePathData(stroke: Stroke): string {
  const pts = stroke.points;
  if (!pts.length) return '';
  const radii = strokeRadii(stroke);
  const outline = strokeOutline(stroke);
  if (!outline) return dotPathData(pts[0].x, pts[0].y, radii[0]);

  const { left, right } = outline;
  const last = pts.length - 1;
  const out: string[] = [`M${f(left[0].x)} ${f(left[0].y)}`];
  for (let i = 1; i < pts.length; i++) {
    out.push(`L${f(left[i].x)} ${f(left[i].y)}`);
  }
  out.push(
    `A${f(radii[last])} ${f(radii[last])} 0 0 0 ${f(right[last].x)} ${f(right[last].y)}`
  );
  for (let i = last - 1; i >= 0; i--) {
    out.push(`L${f(right[i].x)} ${f(right[i].y)}`);
  }
  out.push(
    `A${f(radii[0])} ${f(radii[0])} 0 0 0 ${f(left[0].x)} ${f(left[0].y)}`
  );
  out.push('Z');
  return out.join('');
}

/** The `points` attribute of a flat-width stroke (the highlighter), which needs
 * no ribbon: one polyline, composited once, so overlapping segments inside a
 * single stroke cannot stack their alpha into dark blobs. */
export function polylinePoints(stroke: Stroke): string {
  return stroke.points.map(p => `${f(p.x)},${f(p.y)}`).join(' ');
}
