import { describe, expect, it } from 'vitest';
import {
  canRedo,
  canUndo,
  commitOn,
  emptyMarkup,
  fromWire,
  redoLast,
  setStrokesOn,
  strokesOn,
  toInkStroke,
  toWire,
  toWireStroke,
  undoLast,
  type WireStroke,
} from './newspaperMarkup';
import { eraseStroke, strokeWidth, type Stroke } from './ink';

const ink = (
  points: [number, number][],
  over: Partial<Stroke> = {}
): Stroke => ({
  tool: 'pen',
  size: 2,
  points: points.map(([x, y]) => ({ x, y, pressure: 1 })),
  ...over,
});

describe('reading markup written before any of this existed', () => {
  it('gives an unpressured point full width, so old marks do not thin', () => {
    // The reader drew every stroke at a flat `strokeWidth={2}`. Pressure now
    // modulates that, and `strokeWidth(2, 0.5)` is 1.35 — so defaulting a
    // stored point to the 0.5 a mouse reports would quietly shave a third off
    // every mark ever made.
    const [stroke] = strokesOn(
      fromWire([{ page: 1, tool: 'pen', points: [[0.1, 0.2]] }]),
      1
    );
    expect(stroke.points[0].pressure).toBe(1);
    expect(strokeWidth(stroke.size, stroke.points[0].pressure)).toBe(2);
  });

  it('gives a stroke with no width the width it was drawn at', () => {
    const markup = fromWire([
      { page: 1, tool: 'pen', points: [[0, 0]] },
      { page: 1, tool: 'highlight', points: [[0, 0]] },
    ]);
    expect(strokesOn(markup, 1).map(s => s.size)).toEqual([2, 16]);
  });

  it('maps the stored tool name onto the shared model and back again', () => {
    const markup = fromWire([{ page: 2, tool: 'highlight', points: [[0, 0]] }]);
    expect(strokesOn(markup, 2)[0].tool).toBe('highlighter');
    expect(toWire(markup)[0].tool).toBe('highlight');
  });

  it('keeps a pressure and a colour that were stored', () => {
    const [stroke] = strokesOn(
      fromWire([
        { page: 1, tool: 'pen', points: [[0.5, 0.5, 0.25]], color: '#c0392b' },
      ]),
      1
    );
    expect(stroke.points[0].pressure).toBe(0.25);
    expect(stroke.color).toBe('#c0392b');
  });

  it('drops malformed strokes rather than throwing', () => {
    const markup = fromWire([
      null,
      { page: 0, tool: 'pen', points: [[0, 0]] },
      { page: 1, tool: 'pen', points: 'nope' },
      {
        page: 1,
        tool: 'pen',
        points: [
          ['a', 0],
          [0.4, 0.4],
        ],
      },
    ] as unknown[]);
    expect(strokesOn(markup, 1)).toHaveLength(1);
    expect(strokesOn(markup, 1)[0].points).toHaveLength(1);
  });

  it('cannot be undone: it is not this session to rewind', () => {
    // Undo used to peel strokes off markup loaded from the server, including
    // ones on a page you could not see. The eraser is the answer for old ink.
    expect(
      canUndo(fromWire([{ page: 1, tool: 'pen', points: [[0, 0]] }]))
    ).toBe(false);
  });
});

describe('the page aspect ratio cancels out', () => {
  // A page holds the default 1.3 until pdf.js reports its real aspect. If that
  // stale ratio could reach storage, every stroke drawn in the first moments
  // after opening an issue would land in the wrong place forever.
  it('stores the same point whatever ratio was current when it was drawn', () => {
    const captured = (ratio: number): Stroke => ({
      tool: 'pen',
      size: 2,
      // What the page computes from the pointer: a fraction of the box, times
      // the ink space that ratio defines.
      points: [{ x: 0.25 * 1000, y: 0.6 * 1000 * ratio, pressure: 1 }],
    });
    for (const ratio of [1.3, 1.4142, 2]) {
      const stored = toWireStroke(captured(ratio), ratio).points[0];
      expect(stored.x).toBeCloseTo(0.25, 10);
      expect(stored.y).toBeCloseTo(0.6, 10);
    }
  });

  it('round-trips a stored point back to where it was drawn', () => {
    const ratio = 1.4142;
    const stored = ink([[0.25, 0.6]]);
    const back = toWireStroke(toInkStroke(stored, ratio), ratio);
    expect(back.points[0].x).toBeCloseTo(0.25, 10);
    expect(back.points[0].y).toBeCloseTo(0.6, 10);
  });

  it('survives a page whose ratio is not known at all', () => {
    expect(toWireStroke(ink([[10, 20]]), 0).points[0].y).toBe(0);
  });
});

describe('an eraser reaches only the page it is scrubbing', () => {
  // Coordinates are normalised per page, so (0.5, 0.5) exists on every page in
  // the issue. Erasing has to be page-local or a scrub on page 3 would silently
  // delete the middle of page 7.
  it('leaves an identically-placed stroke on another page alone', () => {
    let markup = emptyMarkup();
    markup = commitOn(markup, 3, ink([[0.5, 0.5]]));
    markup = commitOn(markup, 7, ink([[0.5, 0.5]]));

    const ratio = 1.4;
    const onPage3 = strokesOn(markup, 3).map(s => toInkStroke(s, ratio));
    const rubbed = eraseStroke(
      { strokes: onPage3, history: [], redo: [] },
      ink([[0.5 * 1000, 0.5 * 1000 * ratio]], { tool: 'eraser', size: 60 })
    );
    markup = setStrokesOn(
      markup,
      3,
      rubbed.strokes.map(s => toWireStroke(s, ratio))
    );

    expect(strokesOn(markup, 3)).toHaveLength(0);
    expect(strokesOn(markup, 7)).toHaveLength(1);
  });
});

describe('undo across pages', () => {
  it('rewinds the last edit wherever it was made', () => {
    let markup = emptyMarkup();
    markup = commitOn(markup, 1, ink([[0.1, 0.1]]));
    markup = commitOn(markup, 5, ink([[0.2, 0.2]]));

    markup = undoLast(markup);
    expect(strokesOn(markup, 5)).toHaveLength(0);
    expect(strokesOn(markup, 1)).toHaveLength(1);

    markup = undoLast(markup);
    expect(strokesOn(markup, 1)).toHaveLength(0);
    expect(canUndo(markup)).toBe(false);
  });

  it('redoes onto the page the undone edit came from', () => {
    let markup = commitOn(emptyMarkup(), 5, ink([[0.2, 0.2]]));
    markup = undoLast(markup);
    expect(canRedo(markup)).toBe(true);
    markup = redoLast(markup);
    expect(strokesOn(markup, 5)).toHaveLength(1);
  });

  // The subtle one. Clearing only the edited page's redo would leave the global
  // stack naming page 7, whose own redo list had moved on — and Redo would then
  // resurrect a stroke from a different edit, on a page you were not looking at.
  it('a new edit anywhere discards every page pending redo', () => {
    let markup = emptyMarkup();
    markup = commitOn(markup, 7, ink([[0.3, 0.3]]));
    markup = undoLast(markup);
    expect(canRedo(markup)).toBe(true);

    markup = commitOn(markup, 3, ink([[0.4, 0.4]]));
    expect(canRedo(markup)).toBe(false);

    markup = redoLast(markup);
    expect(strokesOn(markup, 7)).toHaveLength(0);
    expect(strokesOn(markup, 3)).toHaveLength(1);
  });

  it('does nothing at the ends rather than throwing', () => {
    expect(canUndo(undoLast(emptyMarkup()))).toBe(false);
    expect(canRedo(redoLast(emptyMarkup()))).toBe(false);
  });
});

describe('what goes back to the server', () => {
  it('is ordered by page, then by the order the ink was laid down', () => {
    let markup = emptyMarkup();
    markup = commitOn(markup, 9, ink([[0.1, 0.1]], { color: '#111111' }));
    markup = commitOn(markup, 2, ink([[0.2, 0.2]]));
    markup = commitOn(markup, 9, ink([[0.3, 0.3]]));

    const wire = toWire(markup);
    expect(wire.map(s => s.page)).toEqual([2, 9, 9]);
    expect(wire[1].color).toBe('#111111');
    expect(wire[2].color).toBeUndefined();
  });

  it('round-trips through storage unchanged', () => {
    const markup = commitOn(
      emptyMarkup(),
      4,
      ink([[0.125, 0.25]], { tool: 'highlighter', size: 16 })
    );
    const wire = toWire(markup) as WireStroke[];
    const [reloaded] = strokesOn(fromWire(wire), 4);
    expect(reloaded.tool).toBe('highlighter');
    expect(reloaded.size).toBe(16);
    expect(reloaded.points[0]).toEqual({ x: 0.125, y: 0.25, pressure: 1 });
  });
});
