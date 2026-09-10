import { describe, expect, it } from 'vitest';
import { createCanvas, Path2D } from '@napi-rs/canvas';
import { dotPathData, strokePathData } from './inkPath';
import { parseStrokes, serializeStrokes, type Stroke } from './ink';

const line = (
  points: [number, number, number?][],
  over: Partial<Stroke> = {}
): Stroke => ({
  tool: 'pen',
  size: 10,
  points: points.map(([x, y, pressure = 1]) => ({ x, y, pressure })),
  ...over,
});

function raster(stroke: Stroke) {
  const ctx = createCanvas(240, 240).getContext('2d');
  ctx.fill(new Path2D(strokePathData(stroke)));
  return (x: number, y: number) => ctx.getImageData(x, y, 1, 1).data[3];
}

describe('rendered ink', () => {
  it('fills the full retraced line without a waist or hole', () => {
    const alpha = raster(
      line([
        [20, 100],
        [200, 100],
        [20, 100],
      ])
    );
    for (let x = 25; x < 195; x++) {
      for (let y = 97; y <= 102; y++) expect(alpha(x, y)).toBeGreaterThan(240);
    }
  });

  it('keeps a sharp corner filled', () => {
    const alpha = raster(
      line([
        [20, 100],
        [150, 100],
        [150, 200],
      ])
    );
    expect(alpha(149, 100)).toBeGreaterThan(240);
    expect(alpha(145, 100)).toBeGreaterThan(240);
    expect(alpha(150, 105)).toBeGreaterThan(240);
    // Smoothing must not spread the corner into a triangular blob.
    expect(alpha(120, 106)).toBe(0);
    expect(alpha(140, 110)).toBe(0);
  });

  it.each([0, 0.5, 1])('retains the old width at pressure %s', pressure => {
    const alpha = raster(
      line([
        [20, 100, pressure],
        [200, 100, pressure],
      ])
    );
    let coverage = 0;
    for (let y = 80; y < 120; y++) coverage += alpha(100, y) / 255;
    expect(coverage).toBeCloseTo(10 * (0.35 + 0.65 * pressure), 1);
  });

  it('uses real pressure to widen along a stroke', () => {
    const alpha = raster(
      line([
        [20, 100, 0],
        [100, 100, 0.5],
        [200, 100, 1],
      ])
    );
    expect(alpha(40, 103)).toBe(0);
    expect(alpha(180, 103)).toBeGreaterThan(240);
  });

  it('ignores pressure for a highlighter', () => {
    const pts: [number, number, number?][] = [
      [20, 100, 0],
      [100, 100, 1],
      [200, 100, 0.5],
    ];
    expect(strokePathData(line(pts, { tool: 'highlighter' }))).toBe(
      strokePathData(
        line(
          pts.map(([x, y]) => [x, y, 1]),
          { tool: 'highlighter' }
        )
      )
    );
  });

  it('uses the same path after saving and reloading', () => {
    const s = line([
      [20, 30, 0.2],
      [80, 90, 0.8],
      [160, 40, 0.5],
    ]);
    expect(strokePathData(parseStrokes(serializeStrokes([s]))[0])).toBe(
      strokePathData(s)
    );
  });

  it('centres taps and repeated points exactly', () => {
    const d = dotPathData(50, 60, 5);
    expect(strokePathData(line([[50, 60]]))).toBe(d);
    expect(
      strokePathData(
        line([
          [50, 60],
          [50, 60],
        ])
      )
    ).toBe(d);
    expect(raster(line([[50, 60]]))(50, 60)).toBe(255);
  });

  it('renders a tiny stroke and tolerates duplicated samples', () => {
    for (const s of [
      line([
        [50, 60],
        [50.1, 60.1],
      ]),
      line([
        [50, 60],
        [50, 60],
        [100, 60],
        [100, 60],
        [100, 100],
      ]),
    ]) {
      expect(strokePathData(s)).not.toMatch(/NaN|Infinity/);
      expect(raster(s)(50, 60)).toBeGreaterThan(240);
    }
  });

  it('emits nothing for empty strokes or erasers', () => {
    expect(strokePathData(line([]))).toBe('');
    expect(strokePathData(line([[50, 60]], { tool: 'eraser' }))).toBe('');
    expect(dotPathData(0, 0, 0)).toBe('');
  });
});
