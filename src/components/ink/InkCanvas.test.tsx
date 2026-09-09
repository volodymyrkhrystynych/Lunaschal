// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createRef, useRef } from 'react';
import { fireEvent, render, waitFor } from '@testing-library/react';
import { InkCanvas, type InkCanvasHandle } from './InkCanvas';
import type { InkPalette, Stroke } from '@/lib/ink';

const PALETTE: InkPalette = {
  ink: '#111111',
  highlight: '#ffe14d',
  highlightAlpha: 0.4,
};
const SPACE = { width: 1000, height: 1000 };
const NO_STROKES: Stroke[] = [];

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
    clearRect: vi.fn(),
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
  HTMLCanvasElement.prototype.getBoundingClientRect = () =>
    ({ left: 0, top: 0, width: 500, height: 500 }) as DOMRect;
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
});

type Props = Partial<React.ComponentProps<typeof InkCanvas>>;

function renderInk(over: Props = {}) {
  const ref = createRef<InkCanvasHandle>();
  const view = render(
    <InkCanvas
      ref={ref}
      space={SPACE}
      strokes={NO_STROKES}
      tool="pen"
      size={10}
      palette={PALETTE}
      touchPolicy="exclusive"
      {...over}
    />
  );
  const canvas = view.container.querySelector('canvas')!;
  return { ...view, ref, canvas };
}

/** A pointer event of any kind, dispatched the way a browser would. */
function send(target: Element, name: string, fields: Record<string, unknown>) {
  const event = new Event(name, { bubbles: true, cancelable: true });
  Object.assign(event, {
    pointerId: 1,
    isPrimary: true,
    button: 0,
    buttons: 1,
    pressure: 0.5,
    clientX: 0,
    clientY: 0,
    ...fields,
  });
  fireEvent(target, event);
  return event;
}

const pen = (target: Element, fields: Record<string, unknown> = {}) => {
  send(target, 'pointerdown', {
    pointerType: 'pen',
    clientX: 50,
    clientY: 50,
    ...fields,
  });
  send(target, 'pointermove', {
    pointerType: 'pen',
    clientX: 250,
    clientY: 250,
    ...fields,
  });
  send(target, 'pointerup', {
    pointerType: 'pen',
    clientX: 250,
    clientY: 250,
    ...fields,
  });
};

const strokesOf = (ref: React.RefObject<InkCanvasHandle | null>) =>
  ref.current!.getState().strokes;

describe('what may lay down ink', () => {
  it('draws for a pen', async () => {
    const { ref, canvas } = renderInk();
    await waitFor(() => expect(ctx.setTransform).toHaveBeenCalled());
    pen(canvas);
    expect(strokesOf(ref)).toHaveLength(1);
  });

  // Palm rejection, and the reason a hand can rest on the page at all.
  it('never draws for a finger, on either policy', async () => {
    for (const touchPolicy of ['exclusive', 'scroll'] as const) {
      const { ref, canvas } = renderInk({ touchPolicy });
      await waitFor(() => expect(ctx.setTransform).toHaveBeenCalled());
      send(canvas, 'pointerdown', {
        pointerType: 'touch',
        clientX: 50,
        clientY: 50,
      });
      send(canvas, 'pointermove', {
        pointerType: 'touch',
        clientX: 250,
        clientY: 250,
      });
      send(canvas, 'pointerup', {
        pointerType: 'touch',
        clientX: 250,
        clientY: 250,
      });
      expect(strokesOf(ref)).toHaveLength(0);
    }
  });

  it('ignores a palm landing while the pen is already down', async () => {
    const { ref, canvas } = renderInk();
    await waitFor(() => expect(ctx.setTransform).toHaveBeenCalled());
    send(canvas, 'pointerdown', {
      pointerType: 'pen',
      clientX: 50,
      clientY: 50,
    });
    send(canvas, 'pointerdown', {
      pointerType: 'touch',
      pointerId: 2,
      clientX: 300,
      clientY: 300,
    });
    send(canvas, 'pointerup', {
      pointerType: 'touch',
      pointerId: 2,
      clientX: 300,
      clientY: 300,
    });
    send(canvas, 'pointerup', {
      pointerType: 'pen',
      clientX: 250,
      clientY: 250,
    });
    expect(strokesOf(ref)).toHaveLength(1);
  });

  it('marks nothing at all in a read mode', async () => {
    const { ref, canvas } = renderInk({ tool: null });
    await waitFor(() => expect(ctx.setTransform).toHaveBeenCalled());
    pen(canvas);
    expect(strokesOf(ref)).toHaveLength(0);
  });

  it('refuses a pointer that says it is not the primary one', async () => {
    const { ref, canvas } = renderInk();
    await waitFor(() => expect(ctx.setTransform).toHaveBeenCalled());
    pen(canvas, { isPrimary: false });
    expect(strokesOf(ref)).toHaveLength(0);
  });
});

describe('pen pressure', () => {
  it('reaches the stored points', async () => {
    const { ref, canvas } = renderInk();
    await waitFor(() => expect(ctx.setTransform).toHaveBeenCalled());
    pen(canvas, { pressure: 0.25 });
    expect(strokesOf(ref)[0].points.every(p => p.pressure === 0.25)).toBe(true);
  });

  // A mouse reports no pressure at all, and a flat line is the right answer for
  // one — not a hairline.
  it('reads a pointer with none as half', async () => {
    const { ref, canvas } = renderInk();
    await waitFor(() => expect(ctx.setTransform).toHaveBeenCalled());
    send(canvas, 'pointerdown', {
      pointerType: 'mouse',
      pressure: 0,
      clientX: 50,
      clientY: 50,
    });
    send(canvas, 'pointerup', {
      pointerType: 'mouse',
      pressure: 0,
      clientX: 50,
      clientY: 50,
    });
    expect(strokesOf(ref)[0].points[0].pressure).toBe(0.5);
  });
});

describe('a stylus with buttons', () => {
  // A Wacom/Surface/XP-Pen barrel button or inverted eraser tip erases for the
  // length of that stroke only, without disturbing the chosen tool.
  it('erases while the barrel is held, then hands the pen back', async () => {
    const seeded: Stroke[] = [
      { tool: 'pen', size: 10, points: [{ x: 100, y: 100, pressure: 1 }] },
    ];
    const { ref, canvas } = renderInk({ strokes: seeded, size: 200 });
    await waitFor(() => expect(ctx.setTransform).toHaveBeenCalled());
    pen(canvas, { buttons: 2 });
    expect(strokesOf(ref)).toHaveLength(0);

    pen(canvas);
    expect(strokesOf(ref).map(s => s.tool)).toEqual(['pen']);
  });
});

describe('coalesced pointer events', () => {
  // A pen reports far faster than the browser fires events, and the ones in
  // between arrive in a batch. Dropping them is what makes a fast stroke a
  // polygon.
  it('keeps every point the browser held back', async () => {
    const { ref, canvas } = renderInk();
    await waitFor(() => expect(ctx.setTransform).toHaveBeenCalled());
    send(canvas, 'pointerdown', {
      pointerType: 'pen',
      clientX: 10,
      clientY: 10,
    });
    const move = new Event('pointermove', { bubbles: true });
    const at = (x: number) => ({
      clientX: x,
      clientY: x,
      pressure: 0.5,
      pointerId: 1,
      pointerType: 'pen',
    });
    Object.assign(move, {
      ...at(200),
      getCoalescedEvents: () => [at(100), at(200), at(300)],
    });
    fireEvent(canvas, move);
    send(canvas, 'pointerup', {
      pointerType: 'pen',
      clientX: 300,
      clientY: 300,
    });
    expect(strokesOf(ref)[0].points.length).toBe(4);
  });
});

describe('finger gestures belong to the surface only when nothing scrolls', () => {
  const twoFingerTap = (canvas: Element) => {
    send(canvas, 'pointerdown', {
      pointerType: 'touch',
      pointerId: 1,
      clientX: 100,
      clientY: 100,
    });
    send(canvas, 'pointerdown', {
      pointerType: 'touch',
      pointerId: 2,
      clientX: 160,
      clientY: 100,
    });
    send(canvas, 'pointerup', {
      pointerType: 'touch',
      pointerId: 1,
      clientX: 100,
      clientY: 100,
    });
    send(canvas, 'pointerup', {
      pointerType: 'touch',
      pointerId: 2,
      clientX: 160,
      clientY: 100,
    });
  };
  const swipe = (canvas: Element) => {
    send(canvas, 'pointerdown', {
      pointerType: 'touch',
      pointerId: 1,
      clientX: 300,
      clientY: 100,
    });
    send(canvas, 'pointerup', {
      pointerType: 'touch',
      pointerId: 1,
      clientX: 100,
      clientY: 100,
    });
  };

  it('toggles the eraser on a two-finger tap, and flips on a swipe', async () => {
    const onToggleEraser = vi.fn();
    const onSwipe = vi.fn();
    const { canvas } = renderInk({ onToggleEraser, onSwipe });
    await waitFor(() => expect(ctx.setTransform).toHaveBeenCalled());
    twoFingerTap(canvas);
    expect(onToggleEraser).toHaveBeenCalled();
    swipe(canvas);
    expect(onSwipe).toHaveBeenCalledWith('next');
  });

  // Over a scrolling column every finger is the browser's, so the app claims
  // no gesture at all — a two-finger tap there is the start of a pinch-zoom.
  it('claims neither where the page underneath scrolls', async () => {
    const onToggleEraser = vi.fn();
    const onSwipe = vi.fn();
    const { canvas } = renderInk({
      touchPolicy: 'scroll',
      onToggleEraser,
      onSwipe,
    });
    await waitFor(() => expect(ctx.setTransform).toHaveBeenCalled());
    twoFingerTap(canvas);
    swipe(canvas);
    expect(onToggleEraser).not.toHaveBeenCalled();
    expect(onSwipe).not.toHaveBeenCalled();
  });
});

describe('what sits behind the ink', () => {
  // A surface drawn over something else — the newspaper's PDF page — must not
  // paint the background at all, and the eraser must still work there. It can,
  // because it removes ink geometrically rather than painting over it.
  it('clears to transparent by default, and never fills', async () => {
    const { canvas } = renderInk({ tool: 'eraser', size: 100 });
    await waitFor(() => expect(ctx.clearRect).toHaveBeenCalled());
    pen(canvas);
    expect(ctx.fillRect).not.toHaveBeenCalled();
  });

  it('lets a surface paint its own page instead', async () => {
    const paintBackdrop = vi.fn((c: CanvasRenderingContext2D) => {
      c.fillRect(0, 0, 1, 1);
    });
    renderInk({ paintBackdrop });
    await waitFor(() => expect(paintBackdrop).toHaveBeenCalled());
    expect(ctx.clearRect).not.toHaveBeenCalled();
  });
});

describe('the touch policy', () => {
  it('stops the surface scrolling where it is the whole screen', () => {
    const { canvas } = renderInk({ touchPolicy: 'exclusive' });
    expect(canvas.getAttribute('style')).toContain('touch-action: none');
  });

  it('leaves scrolling and pinch-zoom alone where there is a page under it', () => {
    const { canvas } = renderInk({ touchPolicy: 'scroll' });
    expect(canvas.getAttribute('style')).toContain(
      'touch-action: pan-y pinch-zoom'
    );
  });

  function Guarded(props: Props) {
    const guardRef = useRef<HTMLDivElement>(null);
    return (
      <div ref={guardRef} data-testid="guard">
        <InkCanvas
          space={SPACE}
          strokes={NO_STROKES}
          tool="pen"
          size={10}
          palette={PALETTE}
          touchPolicy="scroll"
          guardRef={guardRef}
          {...props}
        />
      </div>
    );
  }

  const touchMove = (element: Element, ...types: string[]) => {
    const event = new Event('touchmove', { bubbles: true, cancelable: true });
    Object.assign(event, { touches: types.map(touchType => ({ touchType })) });
    element.dispatchEvent(event);
    return event.defaultPrevented;
  };

  it('cancels an all-stylus touch stream so the Pencil writes', () => {
    const { getByTestId } = render(<Guarded />);
    expect(touchMove(getByTestId('guard'), 'stylus')).toBe(true);
  });

  it('leaves a finger, and a finger riding with a stylus, to the browser', () => {
    const { getByTestId } = render(<Guarded />);
    // Only two contacts can pinch-zoom, so a stylus alongside a finger is not
    // the Pencil trying to write.
    expect(touchMove(getByTestId('guard'), 'direct')).toBe(false);
    expect(touchMove(getByTestId('guard'), 'direct', 'stylus')).toBe(false);
  });

  it('lets the Pencil scroll again when no tool is marking', () => {
    const { getByTestId } = render(<Guarded tool={null} />);
    expect(touchMove(getByTestId('guard'), 'stylus')).toBe(false);
  });

  it('holds every touch off while a stroke is in flight', async () => {
    const { getByTestId, container } = render(<Guarded />);
    const canvas = container.querySelector('canvas')!;
    await waitFor(() => expect(ctx.setTransform).toHaveBeenCalled());
    expect(touchMove(getByTestId('guard'), 'direct')).toBe(false);
    send(canvas, 'pointerdown', {
      pointerType: 'pen',
      clientX: 50,
      clientY: 50,
    });
    // A resting palm would otherwise drag the page out from under the nib.
    expect(touchMove(getByTestId('guard'), 'direct')).toBe(true);
    send(canvas, 'pointerup', { pointerType: 'pen', clientX: 50, clientY: 50 });
    expect(touchMove(getByTestId('guard'), 'direct')).toBe(false);
  });

  it('installs no guard where nothing scrolls', () => {
    const { getByTestId } = render(<Guarded touchPolicy="exclusive" />);
    expect(touchMove(getByTestId('guard'), 'stylus')).toBe(false);
  });
});

describe('a cancelled stroke', () => {
  // The two surfaces genuinely want opposite answers. On a page that fills the
  // screen a cancel is a quirk and the ink is real, drawn and visible. Over a
  // scrolling column it means the OS took the pointer to scroll with, and half
  // a stray line dragged across a photograph is worse than no line at all.
  it('is kept where the surface is the screen', async () => {
    const { ref, canvas } = renderInk({ touchPolicy: 'exclusive' });
    await waitFor(() => expect(ctx.setTransform).toHaveBeenCalled());
    send(canvas, 'pointerdown', {
      pointerType: 'pen',
      clientX: 50,
      clientY: 50,
    });
    send(canvas, 'pointercancel', {
      pointerType: 'pen',
      clientX: 90,
      clientY: 90,
    });
    expect(strokesOf(ref)).toHaveLength(1);
  });

  it('is discarded where the page under it scrolls', async () => {
    const { ref, canvas } = renderInk({ touchPolicy: 'scroll' });
    await waitFor(() => expect(ctx.setTransform).toHaveBeenCalled());
    send(canvas, 'pointerdown', {
      pointerType: 'pen',
      clientX: 50,
      clientY: 50,
    });
    send(canvas, 'pointercancel', {
      pointerType: 'pen',
      clientX: 90,
      clientY: 90,
    });
    expect(strokesOf(ref)).toHaveLength(0);
  });
});
