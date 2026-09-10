// Shared geometry for the live SVG, committed ink and Paper's Path2D snapshot.
import { getStroke, type Vec2 } from 'perfect-freehand';
import { strokeWidth, type Stroke } from './ink';

const f = (n: number): string => String(Math.round(n * 100) / 100);
const pair = (p: Vec2): string => `${f(p[0])} ${f(p[1])}`;
const midpoint = (a: Vec2, b: Vec2): Vec2 => [
  (a[0] + b[0]) / 2,
  (a[1] + b[1]) / 2,
];

/** A closed quadratic spline through the outline's edge midpoints. Matching
 * tangents at each midpoint avoids seams, including where the path closes. */
function outlinePath(points: Vec2[]): string {
  if (!points.length) return '';
  const out = [`M${pair(midpoint(points[points.length - 1], points[0]))}`];
  for (let i = 0; i < points.length; i++) {
    out.push(
      `Q${pair(points[i])} ${pair(midpoint(points[i], points[(i + 1) % points.length]))}`
    );
  }
  return out.join('') + 'Z';
}

/** Keep taps exactly centred and at the same pressure width as longer ink. */
export function dotPathData(x: number, y: number, r: number): string {
  if (!(r > 0)) return '';
  return `M${f(x - r)} ${f(y)}A${f(r)} ${f(r)} 0 1 0 ${f(x + r)} ${f(y)}A${f(r)} ${f(r)} 0 1 0 ${f(x - r)} ${f(y)}Z`;
}

export function strokePathData(stroke: Stroke): string {
  const pts = stroke.points;
  if (!pts.length || stroke.tool === 'eraser') return '';
  const pen = stroke.tool === 'pen';
  if (pts.every(p => p.x === pts[0].x && p.y === pts[0].y)) {
    const width = pen ? strokeWidth(stroke.size, pts[0].pressure) : stroke.size;
    return dotPathData(pts[0].x, pts[0].y, width / 2);
  }
  // With thinning=1 Perfect Freehand's diameter is 2 * size * pressure.
  // Remap pressure to retain our existing 0.35..1 width range, including old
  // newspaper points whose pressure defaults to 1. Never simulate pressure
  // from sample spacing: simplification and different devices change it.
  const points: number[][] = [];
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i];
    points.push([a.x, a.y, strokeWidth(1, a.pressure) / 2]);
    const b = pts[i + 1];
    if (!b) continue;
    // Resample sparse segments before fitting curves so corner rounding stays
    // local. Also bypass the library's two-point interpolation, which drops
    // pressure. Bound subdivisions for malformed or extremely thin old ink.
    const distance = Math.hypot(b.x - a.x, b.y - a.y);
    if (!distance) continue;
    const steps = Math.min(
      256,
      Math.max(
        pts.length === 2 ? 2 : 1,
        Math.ceil(distance / Math.max(0.5, stroke.size / 2))
      )
    );
    for (let j = 1; j < steps; j++) {
      const u = j / steps;
      points.push([
        a.x + (b.x - a.x) * u,
        a.y + (b.y - a.y) * u,
        (strokeWidth(1, a.pressure) * (1 - u) +
          strokeWidth(1, b.pressure) * u) /
          2,
      ]);
    }
  }
  return outlinePath(
    getStroke(points, {
      size: stroke.size,
      thinning: pen ? 1 : 0,
      simulatePressure: false,
      streamline: 0,
      smoothing: 0.5,
      // Explicitly identical for preview and saved ink: no endpoint snap on lift.
      last: true,
      start: { cap: true, taper: 0 },
      end: { cap: true, taper: 0 },
    })
  );
}
