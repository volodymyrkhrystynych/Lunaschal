import { describe, expect, it } from 'vitest';
import {
  dotPathData,
  polylinePoints,
  strokeOutline,
  strokePathData,
  strokeRadii,
} from './inkPath';
import { strokeWidth, type Stroke } from './ink';

const line = (
  points: [number, number, number?][],
  over: Partial<Stroke> = {}
): Stroke => ({
  tool: 'pen',
  size: 10,
  points: points.map(([x, y, pressure]) => ({
    x,
    y,
    pressure: pressure ?? 1,
  })),
  ...over,
});

/** How wide the ribbon actually is at each point. */
const widths = (stroke: Stroke): number[] => {
  const o = strokeOutline(stroke)!;
  return o.left.map((l, i) =>
    Math.hypot(l.x - o.right[i].x, l.y - o.right[i].y)
  );
};

const numbersIn = (d: string): number[] =>
  (d.match(/-?\d+(\.\d+)?/g) ?? []).map(Number);

describe('the width of the ribbon', () => {
  it('is the stroke width along a straight line', () => {
    for (const w of widths(
      line([
        [0, 0],
        [100, 0],
        [200, 0],
      ])
    )) {
      expect(w).toBeCloseTo(10, 6);
    }
  });

  it('follows the pressure at each point, so the pen tapers', () => {
    const w = widths(
      line([
        [0, 0, 0],
        [100, 0, 0.5],
        [200, 0, 1],
      ])
    );
    expect(w[0]).toBeCloseTo(strokeWidth(10, 0), 6);
    expect(w[1]).toBeCloseTo(strokeWidth(10, 0.5), 6);
    expect(w[2]).toBeCloseTo(strokeWidth(10, 1), 6);
    expect(w[0]).toBeLessThan(w[2]);
  });

  // A highlighter is a flat band: it is one translucent pass, and a tapering
  // edge would read as a smudge rather than a marker.
  it('ignores pressure for the highlighter', () => {
    const flat = line(
      [
        [0, 0, 0],
        [100, 0, 1],
      ],
      { tool: 'highlighter', size: 20 }
    );
    expect(widths(flat)).toEqual([20, 20]);
    expect(strokeRadii(flat)).toEqual([10, 10]);
  });

  // Averaging the two segment normals at a join shortens the offset by
  // cos(θ/2), which pinches a sharp corner to a waist. The stretch puts it back.
  it('does not pinch at a right angle', () => {
    const corner = widths(
      line([
        [0, 0],
        [100, 0],
        [100, 100],
      ])
    );
    expect(corner[1]).toBeGreaterThanOrEqual(10);
  });

  it('caps the stretch where the stroke doubles back on itself', () => {
    // Without a limit this corner's offset goes to infinity and throws a spike
    // across the page.
    for (const w of widths(
      line([
        [0, 0],
        [100, 0],
        [0, 0.001],
      ])
    )) {
      expect(Number.isFinite(w)).toBe(true);
      expect(w).toBeLessThanOrEqual(10 * 2 + 1e-6);
    }
  });
});

describe('strokes with no length', () => {
  it('draws a single point as a dot of the right size', () => {
    const d = strokePathData(line([[50, 60, 1]]));
    expect(strokeOutline(line([[50, 60, 1]]))).toBeNull();
    // Two semicircular arcs back to the start.
    expect(d.match(/A/g)).toHaveLength(2);
    expect(d.endsWith('Z')).toBe(true);
    expect(numbersIn(d).every(Number.isFinite)).toBe(true);
  });

  it('draws a repeated point as a dot rather than nothing', () => {
    const stalled = line([
      [50, 60],
      [50, 60],
      [50, 60],
    ]);
    expect(strokeOutline(stalled)).toBeNull();
    expect(strokePathData(stalled)).toBe(dotPathData(50, 60, 5));
  });

  it('is empty for a stroke with no points at all', () => {
    expect(strokePathData(line([]))).toBe('');
    expect(dotPathData(0, 0, 0)).toBe('');
  });
});

describe('the path a stroke becomes', () => {
  it('closes, and caps both ends', () => {
    const d = strokePathData(
      line([
        [0, 0],
        [100, 0],
      ])
    );
    expect(d.startsWith('M')).toBe(true);
    expect(d.endsWith('Z')).toBe(true);
    // One cap over the tip, one over the start.
    expect(d.match(/A/g)).toHaveLength(2);
  });

  it('never emits a coordinate that is not a number', () => {
    // A duplicated point mid-stroke used to produce a zero-length segment, a
    // zero-length normal, and NaN all the way through the path.
    const awkward = line([
      [0, 0],
      [0, 0],
      [100, 0],
      [100, 0],
      [100, 100],
    ]);
    const d = strokePathData(awkward);
    expect(d).not.toMatch(/NaN|Infinity/);
    expect(numbersIn(d).every(Number.isFinite)).toBe(true);
  });

  it('walks up one side and back down the other', () => {
    // Two points a side, plus the two arcs' endpoints.
    const d = strokePathData(
      line([
        [0, 0],
        [100, 0],
      ])
    );
    expect(d.match(/L/g)).toHaveLength(2);
  });
});

describe('the flat polyline the highlighter uses', () => {
  it('lists every point', () => {
    expect(
      polylinePoints(
        line([
          [0, 0],
          [10.005, 20],
        ])
      )
    ).toBe('0,0 10.01,20');
  });
});
