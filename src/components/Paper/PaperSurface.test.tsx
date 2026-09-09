// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, waitFor } from '@testing-library/react';
import { createRef } from 'react';
import { PaperSurface, type PaperSurfaceHandle } from './PaperSurface';
import { PAGE_HEIGHT, PAGE_WIDTH, type Stroke } from '@/lib/paper';

// No IndexedDB in jsdom, and every test here wants the "no local buffer" path:
// the bug being pinned is about *server* content arriving late.
vi.mock('idb-keyval', () => ({
  get: vi.fn(() => Promise.resolve(undefined)),
  set: vi.fn(() => Promise.resolve()),
  del: vi.fn(() => Promise.resolve()),
}));

/** The page's ink is SVG, but the snapshot it uploads is still rasterized. */
function stubSnapshotCanvas() {
  const ctx = {
    setTransform: vi.fn(),
    save: vi.fn(),
    restore: vi.fn(),
    translate: vi.fn(),
    rotate: vi.fn(),
    scale: vi.fn(),
    drawImage: vi.fn(),
    fillRect: vi.fn(),
    fill: vi.fn(),
    fillStyle: '',
    globalAlpha: 1,
  };
  HTMLCanvasElement.prototype.getContext = vi.fn(
    () => ctx
  ) as unknown as HTMLCanvasElement['getContext'];
  HTMLCanvasElement.prototype.toBlob = vi.fn(cb =>
    cb(new Blob(['x'], { type: 'image/png' }))
  );
  return ctx;
}

beforeEach(() => {
  stubSnapshotCanvas();
  // jsdom implements neither, and the drawing path calls both.
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
  // jsdom lays nothing out, so without this every pointer maps to (0, 0) and
  // the eraser can never reach the ink it is aimed at.
  Element.prototype.getBoundingClientRect = () =>
    ({ left: 0, top: 0, width: 400, height: 566 }) as DOMRect;
});

const stroke = (x: number): Stroke => ({
  tool: 'pen',
  size: 4,
  points: [
    { x, y: 100, pressure: 0.5 },
    { x: x + 50, y: 200, pressure: 0.5 },
  ],
});

const PICTURE = {
  id: 'img-1',
  url: 'blob:x',
  x: 0,
  y: 0,
  width: 100,
  height: 100,
  rotation: 0,
  flipped: false,
  locked: false,
  position: 0,
};

function renderPage(
  initialStrokes: Stroke[],
  over: Partial<React.ComponentProps<typeof PaperSurface>> = {}
) {
  const ref = createRef<PaperSurfaceHandle>();
  const view = render(
    <PaperSurface
      ref={ref}
      pageId="p1"
      initialStrokes={initialStrokes}
      initialSize={{ width: PAGE_WIDTH, height: PAGE_HEIGHT }}
      tool="pen"
      size={4}
      onSwipe={() => {}}
      {...over}
    />
  );
  return { ...view, ref };
}

/** Committed strokes on the page. The stroke in flight is a bare element with
 * no path data until a frame has run, so it is not counted. */
const inked = (container: HTMLElement) =>
  Array.from(container.querySelectorAll('[data-ink-strokes] path')).filter(p =>
    p.getAttribute('d')
  );

/** Draw one stroke with a pen, the way a stylus would. */
function drawOn(svg: Element, over: Record<string, unknown> = {}) {
  const at = (name: string, x: number, y: number) => {
    const event = new Event(name, { bubbles: true });
    Object.assign(event, {
      pointerType: 'pen',
      pointerId: 7,
      isPrimary: true,
      button: 0,
      buttons: 1,
      pressure: 0.5,
      clientX: x,
      clientY: y,
      ...over,
    });
    fireEvent(svg, event);
  };
  at('pointerdown', 10, 10);
  at('pointermove', 40, 60);
  at('pointerup', 40, 60);
}

async function savedStrokes(ref: { current: PaperSurfaceHandle | null }) {
  const data = await ref.current!.getSaveData();
  return JSON.parse(data!.strokes) as Stroke[];
}

describe('a picture changing elsewhere never wipes a stroke in progress', () => {
  it('leaves the stroke in flight alone when the pictures change', async () => {
    // The reported bug, back when the page was a canvas: writing on it
    // stuttered — some of what had just been written erased and came back. The
    // `images` prop changes far more often than it used to now that a local
    // commit rewrites the page's cache every couple of seconds while drawing,
    // and a canvas repaint drew only the *committed* strokes, so a stroke still
    // in flight had nowhere to survive one.
    //
    // On SVG it cannot happen at all: the stroke in flight is its own element
    // and the pictures are their own elements, so a picture arriving re-renders
    // neither the committed ink nor the live path. This pins that.
    const { container, rerender, ref } = renderPage([]);
    const svg = container.querySelector('svg')!;
    await waitFor(() => expect(ref.current).toBeTruthy());

    fireEvent(
      svg,
      Object.assign(new Event('pointerdown', { bubbles: true }), {
        pointerType: 'pen',
        pointerId: 7,
        clientX: 20,
        clientY: 20,
        pressure: 0.5,
      })
    );
    fireEvent(
      svg,
      Object.assign(new Event('pointermove', { bubbles: true }), {
        pointerType: 'pen',
        pointerId: 7,
        clientX: 60,
        clientY: 90,
        pressure: 0.5,
      })
    );
    // Let the frame that paints the live stroke run.
    await new Promise(r => setTimeout(r, 20));
    const live = svg.querySelector('[data-ink-strokes] path:last-of-type')!;
    expect(live.getAttribute('d')).toBeTruthy();

    rerender(
      <PaperSurface
        ref={ref}
        pageId="p1"
        images={[PICTURE]}
        initialStrokes={[]}
        initialSize={{ width: PAGE_WIDTH, height: PAGE_HEIGHT }}
        tool="pen"
        size={4}
        onSwipe={() => {}}
      />
    );

    // The picture is on the page, and the stroke being drawn is untouched.
    expect(container.querySelector('[data-ink-backdrop] image')).toBeTruthy();
    expect(live.getAttribute('d')).toBeTruthy();

    fireEvent(
      svg,
      Object.assign(new Event('pointerup', { bubbles: true }), {
        pointerType: 'pen',
        pointerId: 7,
        clientX: 60,
        clientY: 90,
        pressure: 0.5,
      })
    );
    expect(await ref.current!.getSaveData()).not.toBeNull();
  });
});

describe('adopting content that arrives after mount', () => {
  it('draws strokes that land while the page is already up', async () => {
    // The reported bug: a page seeded from a stale (pre-save) cache entry came
    // up blank, and the refetch that followed was dropped because the seeding
    // effect only ever ran on mount.
    const { container, rerender, ref } = renderPage([]);
    await waitFor(() => expect(ref.current).toBeTruthy());
    expect(inked(container)).toHaveLength(0);

    rerender(
      <PaperSurface
        ref={ref}
        pageId="p1"
        initialStrokes={[stroke(10)]}
        initialSize={{ width: PAGE_WIDTH, height: PAGE_HEIGHT }}
        tool="pen"
        size={4}
        onSwipe={() => {}}
      />
    );

    await waitFor(() => expect(inked(container)).toHaveLength(1));
  });

  it('ignores a re-render that carries the same strokes', async () => {
    const strokes = [stroke(10)];
    const { container, rerender, ref } = renderPage(strokes);
    await waitFor(() => expect(inked(container)).toHaveLength(1));
    const first = inked(container)[0];

    // Same array identity — a plain parent re-render, not new data. Re-seeding
    // here would silently discard undo history.
    rerender(
      <PaperSurface
        ref={ref}
        pageId="p1"
        initialStrokes={strokes}
        initialSize={{ width: PAGE_WIDTH, height: PAGE_HEIGHT }}
        tool="pen"
        size={4}
        onSwipe={() => {}}
      />
    );

    // The very same element, so nothing was re-derived and no state was reset.
    expect(inked(container)[0]).toBe(first);
  });

  it('never lets arriving content overwrite unsaved strokes', async () => {
    const { container, rerender, ref } = renderPage([]);
    await waitFor(() => expect(ref.current).toBeTruthy());
    drawOn(container.querySelector('svg')!);

    const dirty = await ref.current!.getSaveData();
    // Guard the guard: if the synthetic pointer sequence stopped registering as
    // a stroke this test would pass while asserting nothing.
    expect(dirty).not.toBeNull();

    rerender(
      <PaperSurface
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

describe('the colour a stroke is drawn in', () => {
  it('is stored on the stroke, not on the page', async () => {
    // Colour has to travel with the stroke: changing the pen colour must
    // restyle nothing already written, and a page holds strokes of several
    // colours at once.
    const { container, ref } = renderPage([], { color: '#c0392b' });
    await waitFor(() => expect(ref.current).toBeTruthy());
    drawOn(container.querySelector('svg')!);
    const [drawnStroke] = await savedStrokes(ref);
    expect(drawnStroke.color).toBe('#c0392b');
    expect(inked(container)[0].getAttribute('fill')).toBe('#c0392b');
  });

  it('is left off a stroke drawn at the surface default', async () => {
    // Absent means "whatever this surface calls ink". Every stroke written
    // before there was a picker has no colour, and writing one in now would
    // make those two states indistinguishable.
    const { container, ref } = renderPage([]);
    await waitFor(() => expect(ref.current).toBeTruthy());
    drawOn(container.querySelector('svg')!);
    const [drawnStroke] = await savedStrokes(ref);
    expect(drawnStroke.color).toBeUndefined();
    expect(inked(container)[0].getAttribute('fill')).toBe('#111111');
  });

  it('is left off an eraser stroke, which lays down no ink', async () => {
    const { container, ref } = renderPage([stroke(100)], {
      tool: 'eraser',
      size: 60,
      color: '#c0392b',
    });
    // The page's strokes are seeded asynchronously (the on-device buffer is
    // looked for first), so wait until there is ink to rub out.
    await waitFor(() => expect(inked(container)).toHaveLength(1));
    drawOn(container.querySelector('svg')!);
    // The eraser is consumed rather than stored, so nothing it touched can
    // have picked up a colour from it.
    for (const s of await savedStrokes(ref)) expect(s.color).toBeUndefined();
  });
});

describe('the page snapshot', () => {
  it('is a fixed size, whatever the page happens to be on screen', async () => {
    // It used to be the canvas's on-screen size times the device pixel ratio,
    // so the same page produced a different thumbnail on a laptop and a phone.
    const { container, ref } = renderPage([]);
    await waitFor(() => expect(ref.current).toBeTruthy());
    drawOn(container.querySelector('svg')!);
    const data = await ref.current!.getSaveData();
    expect(data!.width).toBe(PAGE_WIDTH);
    expect(data!.height).toBe(PAGE_HEIGHT);
    expect(data!.snapshot).toBeInstanceOf(Blob);
  });

  it('is not produced at all for a page nobody has touched', async () => {
    const { ref } = renderPage([stroke(10)]);
    await waitFor(() => expect(ref.current).toBeTruthy());
    expect(await ref.current!.getSaveData()).toBeNull();
  });
});
