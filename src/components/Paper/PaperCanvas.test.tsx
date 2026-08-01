// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import { createRef } from 'react';
import { PaperCanvas, type PaperCanvasHandle } from './PaperCanvas';
import { PAGE_HEIGHT, PAGE_WIDTH, type Stroke } from '@/lib/paper';

// No IndexedDB in jsdom, and every test here wants the "no local buffer" path:
// the bug being pinned is about *server* content arriving late.
vi.mock('idb-keyval', () => ({
  get: vi.fn(() => Promise.resolve(undefined)),
  set: vi.fn(() => Promise.resolve()),
  del: vi.fn(() => Promise.resolve()),
}));

function stubContext() {
  return {
    setTransform: vi.fn(),
    save: vi.fn(),
    restore: vi.fn(),
    beginPath: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    arc: vi.fn(),
    fill: vi.fn(),
    fillRect: vi.fn(),
    stroke: vi.fn(),
    fillStyle: '',
    strokeStyle: '',
    lineWidth: 0,
    lineCap: '',
    lineJoin: '',
    globalAlpha: 1,
  };
}

let ctx: ReturnType<typeof stubContext>;

beforeEach(() => {
  ctx = stubContext();
  HTMLCanvasElement.prototype.getContext = vi.fn(
    () => ctx
  ) as unknown as HTMLCanvasElement['getContext'];
  HTMLCanvasElement.prototype.toBlob = vi.fn(cb =>
    cb(new Blob(['x'], { type: 'image/png' }))
  );
  // jsdom implements neither, and the drawing path calls both.
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
});

const stroke = (x: number): Stroke => ({
  tool: 'pen',
  size: 4,
  points: [
    { x, y: 100, pressure: 0.5 },
    { x: x + 50, y: 200, pressure: 0.5 },
  ],
});

function renderCanvas(initialStrokes: Stroke[]) {
  const ref = createRef<PaperCanvasHandle>();
  const view = render(
    <PaperCanvas
      ref={ref}
      pageId="p1"
      initialStrokes={initialStrokes}
      initialSize={{ width: PAGE_WIDTH, height: PAGE_HEIGHT }}
      tool="pen"
      size={4}
      onSwipe={() => {}}
    />
  );
  return { ...view, ref };
}

describe('adopting content that arrives after mount', () => {
  it('draws strokes that land while the canvas is already up', async () => {
    // The reported bug: a page seeded from a stale (pre-save) cache entry came
    // up blank, and the refetch that followed was dropped because the seeding
    // effect only ever ran on mount.
    const { rerender } = renderCanvas([]);
    await waitFor(() => expect(ctx.setTransform).toHaveBeenCalled());
    expect(ctx.stroke).not.toHaveBeenCalled();

    rerender(
      <PaperCanvas
        pageId="p1"
        initialStrokes={[stroke(10)]}
        initialSize={{ width: PAGE_WIDTH, height: PAGE_HEIGHT }}
        tool="pen"
        size={4}
        onSwipe={() => {}}
      />
    );

    await waitFor(() => expect(ctx.stroke).toHaveBeenCalled());
  });

  it('ignores a re-render that carries the same strokes', async () => {
    const strokes = [stroke(10)];
    const { rerender } = renderCanvas(strokes);
    await waitFor(() => expect(ctx.stroke).toHaveBeenCalled());
    const drawnOnMount = ctx.stroke.mock.calls.length;

    // Same array identity — a plain parent re-render, not new data. Re-seeding
    // here would silently discard undo history.
    rerender(
      <PaperCanvas
        pageId="p1"
        initialStrokes={strokes}
        initialSize={{ width: PAGE_WIDTH, height: PAGE_HEIGHT }}
        tool="pen"
        size={4}
        onSwipe={() => {}}
      />
    );

    expect(ctx.stroke.mock.calls.length).toBe(drawnOnMount);
  });

  it('never lets arriving content overwrite unsaved strokes', async () => {
    const { container, rerender, ref } = renderCanvas([]);
    await waitFor(() => expect(ctx.setTransform).toHaveBeenCalled());
    const canvas = container.querySelector('canvas')!;
    canvas.getBoundingClientRect = () =>
      ({ left: 0, top: 0, width: 400, height: 566 }) as DOMRect;

    // Draw with the pen, leaving the canvas dirty.
    canvas.dispatchEvent(
      new MouseEvent('pointerdown', { bubbles: true, clientX: 20, clientY: 20 })
    );
    canvas.dispatchEvent(
      new MouseEvent('pointermove', { bubbles: true, clientX: 60, clientY: 90 })
    );
    canvas.dispatchEvent(
      new MouseEvent('pointerup', { bubbles: true, clientX: 60, clientY: 90 })
    );

    const dirty = await ref.current!.getSaveData();
    // Guard the guard: if the synthetic pointer sequence stopped registering as
    // a stroke this test would pass while asserting nothing.
    expect(dirty).not.toBeNull();

    rerender(
      <PaperCanvas
        ref={ref}
        pageId="p1"
        initialStrokes={[stroke(999)]}
        initialSize={{ width: PAGE_WIDTH, height: PAGE_HEIGHT }}
        tool="pen"
        size={4}
        onSwipe={() => {}}
      />
    );

    const after = await ref.current!.getSaveData();
    expect(after).not.toBeNull();
    expect(after!.strokes).toBe(dirty!.strokes);
  });
});
