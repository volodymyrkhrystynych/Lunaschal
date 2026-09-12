import { describe, it, expect, vi, afterEach } from 'vitest';
import { paintStrokes } from './inkRaster';
import type { InkPalette, Stroke } from './ink';

const PALETTE: InkPalette = {
  ink: '#1756ad',
  highlight: '#ffe14d',
  highlightAlpha: 0.4,
};

const stroke = (over: Partial<Stroke> = {}): Stroke => ({
  tool: 'pen',
  size: 3,
  points: [
    { x: 0, y: 0, pressure: 0.5 },
    { x: 10, y: 10, pressure: 0.5 },
  ],
  ...over,
});

/** The surface paintStrokes actually touches — the same shape
 * PaperSurface.test.tsx stubs for its snapshot canvas. */
function fakeContext() {
  const calls: { color: string; alpha: number }[] = [];
  const ctx = {
    fillStyle: '',
    globalAlpha: 1,
    fill: vi.fn(() => {
      calls.push({ color: ctx.fillStyle, alpha: ctx.globalAlpha });
    }),
  };
  return { ctx: ctx as unknown as CanvasRenderingContext2D, calls, raw: ctx };
}

/** The node environment has no Path2D, which is the point of one of these
 * tests and in the way of all the others. */
function withPath2D() {
  (globalThis as Record<string, unknown>).Path2D = class {
    constructor(public d: string) {}
  };
}

afterEach(() => {
  delete (globalThis as Record<string, unknown>).Path2D;
});

describe('paintStrokes', () => {
  it('is a no-op rather than a throw where Path2D does not exist', () => {
    const { ctx, raw } = fakeContext();
    expect(() => paintStrokes(ctx, [stroke()], PALETTE)).not.toThrow();
    expect(raw.fill).not.toHaveBeenCalled();
  });

  it('paints a pen at full alpha in the palette ink', () => {
    withPath2D();
    const { ctx, calls } = fakeContext();
    paintStrokes(ctx, [stroke()], PALETTE);
    expect(calls).toEqual([{ color: PALETTE.ink, alpha: 1 }]);
  });

  it('paints a highlighter in the palette highlight at its alpha', () => {
    withPath2D();
    const { ctx, calls } = fakeContext();
    paintStrokes(ctx, [stroke({ tool: 'highlighter' })], PALETTE);
    expect(calls).toEqual([
      { color: PALETTE.highlight, alpha: PALETTE.highlightAlpha },
    ]);
  });

  it("keeps a stroke's own colour when it has one", () => {
    withPath2D();
    const { ctx, calls } = fakeContext();
    paintStrokes(ctx, [stroke({ color: '#c81e1e' })], PALETTE);
    expect(calls[0].color).toBe('#c81e1e');
  });

  it('skips strokes with no path — an eraser leaves no paint', () => {
    withPath2D();
    const { ctx, raw } = fakeContext();
    paintStrokes(
      ctx,
      [stroke({ tool: 'eraser' }), stroke({ points: [] })],
      PALETTE
    );
    expect(raw.fill).not.toHaveBeenCalled();
  });

  it('restores the alpha, so the next thing drawn is not translucent', () => {
    withPath2D();
    const { ctx, raw } = fakeContext();
    paintStrokes(ctx, [stroke({ tool: 'highlighter' })], PALETTE);
    expect(raw.globalAlpha).toBe(1);
  });
});
