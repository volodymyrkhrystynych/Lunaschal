// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createRef, useRef } from 'react';
import { fireEvent, render, waitFor } from '@testing-library/react';
import { InkSurface, type InkSurfaceHandle } from './InkSurface';
import type { InkPalette, Stroke } from '@/lib/ink';

const PALETTE: InkPalette = {
  ink: '#111111',
  highlight: '#ffe14d',
  highlightAlpha: 0.4,
};
const SPACE = { width: 1000, height: 1000 };
const NO_STROKES: Stroke[] = [];

beforeEach(() => {
  // jsdom lays nothing out, so a pointer would otherwise land at (0, 0).
  Element.prototype.getBoundingClientRect = () =>
    ({ left: 0, top: 0, width: 500, height: 500 }) as DOMRect;
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
});

type Props = Partial<React.ComponentProps<typeof InkSurface>>;

function renderInk(over: Props = {}) {
  const ref = createRef<InkSurfaceHandle>();
  const view = render(
    <InkSurface
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
  const svg = view.container.querySelector('svg')!;
  return { ...view, ref, svg };
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

const strokesOf = (ref: React.RefObject<InkSurfaceHandle | null>) =>
  ref.current!.getState().strokes;

/** Committed strokes carry path data; the in-flight element has none until a
 * frame has passed. */
const drawn = (svg: Element) =>
  Array.from(svg.querySelectorAll('[data-ink-strokes] path')).filter(p =>
    p.getAttribute('d')
  );

/** Let the coalescing animation frame run. */
const frame = () => new Promise(resolve => setTimeout(resolve, 20));

describe('what may lay down ink', () => {
  it('draws for a pen, as one path per stroke', () => {
    const { ref, svg } = renderInk();
    pen(svg);
    expect(strokesOf(ref)).toHaveLength(1);
    expect(drawn(svg)).toHaveLength(1);
  });

  // Palm rejection, and the reason a hand can rest on the page at all.
  it('never draws for a finger, on either policy', () => {
    for (const touchPolicy of ['exclusive', 'scroll'] as const) {
      const { ref, svg } = renderInk({ touchPolicy });
      send(svg, 'pointerdown', {
        pointerType: 'touch',
        clientX: 50,
        clientY: 50,
      });
      send(svg, 'pointermove', {
        pointerType: 'touch',
        clientX: 250,
        clientY: 250,
      });
      send(svg, 'pointerup', {
        pointerType: 'touch',
        clientX: 250,
        clientY: 250,
      });
      expect(strokesOf(ref)).toHaveLength(0);
      expect(drawn(svg)).toHaveLength(0);
    }
  });

  it('ignores a palm landing while the pen is already down', () => {
    const { ref, svg } = renderInk();
    send(svg, 'pointerdown', { pointerType: 'pen', clientX: 50, clientY: 50 });
    send(svg, 'pointerdown', {
      pointerType: 'touch',
      pointerId: 2,
      clientX: 300,
      clientY: 300,
    });
    send(svg, 'pointerup', {
      pointerType: 'touch',
      pointerId: 2,
      clientX: 300,
      clientY: 300,
    });
    send(svg, 'pointerup', { pointerType: 'pen', clientX: 250, clientY: 250 });
    expect(strokesOf(ref)).toHaveLength(1);
  });

  it('marks nothing at all in a read mode', () => {
    const { ref, svg } = renderInk({ tool: null });
    pen(svg);
    expect(strokesOf(ref)).toHaveLength(0);
  });

  it('refuses a pointer that says it is not the primary one', () => {
    const { ref, svg } = renderInk();
    pen(svg, { isPrimary: false });
    expect(strokesOf(ref)).toHaveLength(0);
  });
});

describe('how a stroke is painted', () => {
  it('fills the pen in its own colour', () => {
    const { svg } = renderInk({ color: '#c0392b' });
    pen(svg);
    expect(drawn(svg)[0].getAttribute('fill')).toBe('#c0392b');
  });

  it('falls back to the surface palette when a stroke names no colour', () => {
    const { svg } = renderInk();
    pen(svg);
    expect(drawn(svg)[0].getAttribute('fill')).toBe('#111111');
  });

  // One element, composited once, so a highlighter doubling back over itself
  // cannot stack its alpha into a dark blob.
  it('gives the highlighter a single translucent pass', () => {
    const { svg } = renderInk({ tool: 'highlighter', size: 40 });
    pen(svg);
    const path = drawn(svg)[0];
    expect(path.getAttribute('opacity')).toBe('0.4');
    expect(path.getAttribute('fill')).toBe('#ffe14d');
  });

  it('scales with the box rather than being redrawn for it', () => {
    // The whole reason for SVG: the ink is geometry in the page's own units,
    // so it is resolution-independent and stays sharp at any magnification.
    const { svg } = renderInk();
    expect(svg.getAttribute('viewBox')).toBe('0 0 1000 1000');
  });
});

describe('a stroke in flight', () => {
  it('is drawn on its own element, so a re-render cannot wipe it', async () => {
    // On the old canvas this was a real bug: in-progress ink was painted
    // straight onto the bitmap and was not in the committed state yet, so any
    // repaint — a picture finishing its upload, say — erased what had just
    // been written until the stroke ended.
    const { svg, rerender, ref } = renderInk();
    send(svg, 'pointerdown', { pointerType: 'pen', clientX: 50, clientY: 50 });
    send(svg, 'pointermove', {
      pointerType: 'pen',
      clientX: 250,
      clientY: 250,
    });
    await frame();
    const live = svg.querySelector('[data-ink-strokes] path:last-of-type')!;
    expect(live.getAttribute('d')).toBeTruthy();

    rerender(
      <InkSurface
        ref={ref}
        space={SPACE}
        strokes={NO_STROKES}
        tool="pen"
        size={10}
        palette={PALETTE}
        touchPolicy="exclusive"
        backdrop={<rect width={10} height={10} />}
      />
    );
    expect(live.getAttribute('d')).toBeTruthy();

    send(svg, 'pointerup', { pointerType: 'pen', clientX: 250, clientY: 250 });
    expect(strokesOf(ref)).toHaveLength(1);
  });

  it('is cleared once it commits, so it is not drawn twice', async () => {
    const { svg } = renderInk();
    pen(svg);
    await frame();
    expect(drawn(svg)).toHaveLength(1);
  });
});

describe('pen pressure', () => {
  it('reaches the stored points', () => {
    const { ref, svg } = renderInk();
    pen(svg, { pressure: 0.25 });
    expect(strokesOf(ref)[0].points.every(p => p.pressure === 0.25)).toBe(true);
  });

  // A mouse reports no pressure at all, and a flat line is the right answer for
  // one — not a hairline.
  it('reads a pointer with none as half', () => {
    const { ref, svg } = renderInk();
    send(svg, 'pointerdown', {
      pointerType: 'mouse',
      pressure: 0,
      clientX: 50,
      clientY: 50,
    });
    send(svg, 'pointerup', {
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
  it('erases while the barrel is held, then hands the pen back', () => {
    const seeded: Stroke[] = [
      { tool: 'pen', size: 10, points: [{ x: 100, y: 100, pressure: 1 }] },
    ];
    const { ref, svg } = renderInk({ strokes: seeded, size: 200 });
    pen(svg, { buttons: 2 });
    expect(strokesOf(ref)).toHaveLength(0);

    pen(svg);
    expect(strokesOf(ref).map(s => s.tool)).toEqual(['pen']);
  });
});

describe('coalesced pointer events', () => {
  // A pen reports far faster than the browser fires events, and the ones in
  // between arrive in a batch. Dropping them is what makes a fast stroke a
  // polygon.
  it('keeps every point the browser held back', () => {
    const { ref, svg } = renderInk();
    send(svg, 'pointerdown', { pointerType: 'pen', clientX: 10, clientY: 10 });
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
    fireEvent(svg, move);
    send(svg, 'pointerup', { pointerType: 'pen', clientX: 300, clientY: 300 });
    expect(strokesOf(ref)[0].points.length).toBe(4);
  });
});

describe('the eraser', () => {
  const seeded: Stroke[] = [
    {
      tool: 'pen',
      size: 10,
      points: [
        { x: 100, y: 100, pressure: 1 },
        { x: 400, y: 400, pressure: 1 },
      ],
    },
  ];

  it('removes what it is scrubbed over', () => {
    const { ref, svg } = renderInk({
      strokes: seeded,
      tool: 'eraser',
      size: 400,
    });
    pen(svg);
    expect(strokesOf(ref)).toHaveLength(0);
  });

  // The eraser removes ink rather than painting over it, which is the whole
  // reason a surface drawn on top of something else can work at all.
  it('never lays down ink of its own', () => {
    const { ref, svg } = renderInk({ tool: 'eraser', size: 400 });
    pen(svg);
    expect(strokesOf(ref)).toHaveLength(0);
    expect(drawn(svg)).toHaveLength(0);
  });

  it('counts a rub that only shortens a stroke as an edit', async () => {
    // The trap: erasing the tail of a stroke leaves it one stroke, exactly as
    // erasing nothing does. Deciding by stroke count threw those rubs away —
    // the ink came back the moment anything else repainted, and the page did
    // not even read as unsaved. Ink is counted in points, which the eraser
    // removes exactly.
    const long: Stroke[] = [
      {
        tool: 'pen',
        size: 10,
        points: [
          { x: 100, y: 100, pressure: 1 },
          { x: 200, y: 200, pressure: 1 },
          { x: 800, y: 800, pressure: 1 },
        ],
      },
    ];
    const { ref, svg } = renderInk({
      strokes: long,
      tool: 'eraser',
      size: 120,
    });
    // Scrubbed over the far end only: 500,500 in space is 250,250 on screen.
    send(svg, 'pointerdown', {
      pointerType: 'pen',
      clientX: 400,
      clientY: 400,
    });
    send(svg, 'pointerup', { pointerType: 'pen', clientX: 400, clientY: 400 });

    const after = strokesOf(ref);
    expect(after).toHaveLength(1);
    expect(after[0].points).toHaveLength(2);
    expect(ref.current!.getState().dirty).toBe(true);
  });

  it('costs no undo step when it crosses nothing', () => {
    const changed = vi.fn();
    const { svg } = renderInk({
      strokes: seeded,
      tool: 'eraser',
      size: 4,
      onStateChange: changed,
    });
    send(svg, 'pointerdown', { pointerType: 'pen', clientX: 480, clientY: 10 });
    send(svg, 'pointerup', { pointerType: 'pen', clientX: 480, clientY: 10 });
    expect(changed).not.toHaveBeenCalled();
  });
});

describe('what sits behind the ink', () => {
  // A surface drawn over something else — the newspaper's PDF page — must not
  // paint a background at all.
  it('renders nothing behind the ink unless it is given something', () => {
    const { svg } = renderInk();
    expect(svg.querySelector('[data-ink-backdrop]')).toBeNull();
  });

  it('puts a given backdrop under the strokes, in the same space', () => {
    const { svg } = renderInk({
      backdrop: <rect width={1000} height={1000} fill="#ffffff" />,
    });
    const backdrop = svg.querySelector('[data-ink-backdrop]')!;
    const strokes = svg.querySelector('[data-ink-strokes]')!;
    expect(backdrop).toBeTruthy();
    expect(
      backdrop.compareDocumentPosition(strokes) &
        Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();
  });
});

describe('finger gestures belong to the surface only when nothing scrolls', () => {
  const twoFingerTap = (svg: Element) => {
    send(svg, 'pointerdown', {
      pointerType: 'touch',
      pointerId: 1,
      clientX: 100,
      clientY: 100,
    });
    send(svg, 'pointerdown', {
      pointerType: 'touch',
      pointerId: 2,
      clientX: 160,
      clientY: 100,
    });
    send(svg, 'pointerup', {
      pointerType: 'touch',
      pointerId: 1,
      clientX: 100,
      clientY: 100,
    });
    send(svg, 'pointerup', {
      pointerType: 'touch',
      pointerId: 2,
      clientX: 160,
      clientY: 100,
    });
  };
  const swipe = (svg: Element) => {
    send(svg, 'pointerdown', {
      pointerType: 'touch',
      pointerId: 1,
      clientX: 300,
      clientY: 100,
    });
    send(svg, 'pointerup', {
      pointerType: 'touch',
      pointerId: 1,
      clientX: 100,
      clientY: 100,
    });
  };

  it('toggles the eraser on a two-finger tap, and flips on a swipe', () => {
    const onToggleEraser = vi.fn();
    const onSwipe = vi.fn();
    const { svg } = renderInk({ onToggleEraser, onSwipe });
    twoFingerTap(svg);
    expect(onToggleEraser).toHaveBeenCalled();
    swipe(svg);
    expect(onSwipe).toHaveBeenCalledWith('next');
  });

  // Over a scrolling column every finger is the browser's, so the app claims
  // no gesture at all — a two-finger tap there is the start of a pinch-zoom.
  it('claims neither where the page underneath scrolls', () => {
    const onToggleEraser = vi.fn();
    const onSwipe = vi.fn();
    const { svg } = renderInk({
      touchPolicy: 'scroll',
      onToggleEraser,
      onSwipe,
    });
    twoFingerTap(svg);
    swipe(svg);
    expect(onToggleEraser).not.toHaveBeenCalled();
    expect(onSwipe).not.toHaveBeenCalled();
  });
});

describe('the touch policy', () => {
  it('stops the surface scrolling where it is the whole screen', () => {
    const { svg } = renderInk({ touchPolicy: 'exclusive' });
    expect(svg.getAttribute('style')).toContain('touch-action: none');
  });

  it('leaves scrolling and pinch-zoom alone where there is a page under it', () => {
    const { svg } = renderInk({ touchPolicy: 'scroll' });
    expect(svg.getAttribute('style')).toContain(
      'touch-action: pan-y pinch-zoom'
    );
  });

  function Guarded(props: Props) {
    const guardRef = useRef<HTMLDivElement>(null);
    return (
      <div ref={guardRef} data-testid="guard">
        <InkSurface
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
    const svg = container.querySelector('svg')!;
    await waitFor(() => expect(svg).toBeTruthy());
    expect(touchMove(getByTestId('guard'), 'direct')).toBe(false);
    send(svg, 'pointerdown', { pointerType: 'pen', clientX: 50, clientY: 50 });
    // A resting palm would otherwise drag the page out from under the nib.
    expect(touchMove(getByTestId('guard'), 'direct')).toBe(true);
    send(svg, 'pointerup', { pointerType: 'pen', clientX: 50, clientY: 50 });
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
  it('is kept where the surface is the screen', () => {
    const { ref, svg } = renderInk({ touchPolicy: 'exclusive' });
    send(svg, 'pointerdown', { pointerType: 'pen', clientX: 50, clientY: 50 });
    send(svg, 'pointercancel', {
      pointerType: 'pen',
      clientX: 90,
      clientY: 90,
    });
    expect(strokesOf(ref)).toHaveLength(1);
  });

  it('is discarded where the page under it scrolls', () => {
    const { ref, svg } = renderInk({ touchPolicy: 'scroll' });
    send(svg, 'pointerdown', { pointerType: 'pen', clientX: 50, clientY: 50 });
    send(svg, 'pointercancel', {
      pointerType: 'pen',
      clientX: 90,
      clientY: 90,
    });
    expect(strokesOf(ref)).toHaveLength(0);
  });
});
