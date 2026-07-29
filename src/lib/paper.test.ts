import { describe, expect, it } from 'vitest';
import {
  commitStroke,
  emptyStrokeState,
  eraseStroke,
  isHorizontalSwipe,
  isTap,
  parseStrokes,
  redo,
  resolveSwipe,
  serializeStrokes,
  simplifyStroke,
  strokeWidth,
  undo,
  type Stroke,
  type StrokePoint,
  type StrokeState,
} from './paper';

const stroke = (n: number): Stroke => ({
  tool: 'pen',
  size: 4,
  points: [{ x: n, y: n, pressure: 0.5 }],
});

describe('undo/redo', () => {
  it('commits strokes and clears redo history', () => {
    let s = emptyStrokeState();
    s = commitStroke(s, stroke(1));
    s = commitStroke(s, stroke(2));
    expect(s.strokes).toHaveLength(2);
    expect(s.redo).toHaveLength(0);
  });

  it('undo restores the previous snapshot; redo restores the undone state', () => {
    let s = commitStroke(
      commitStroke(emptyStrokeState(), stroke(1)),
      stroke(2)
    );
    s = undo(s);
    expect(s.strokes).toHaveLength(1);
    expect(s.redo).toHaveLength(1);
    s = redo(s);
    expect(s.strokes).toHaveLength(2);
    expect(s.redo).toHaveLength(0);
  });

  it('a new stroke after undo forks the timeline (redo discarded)', () => {
    let s = commitStroke(emptyStrokeState(), stroke(1));
    s = undo(s);
    expect(s.redo).toHaveLength(1);
    s = commitStroke(s, stroke(9));
    expect(s.strokes).toHaveLength(1);
    expect(s.redo).toHaveLength(0);
  });

  it('undo/redo at the boundaries are no-ops', () => {
    const empty = emptyStrokeState();
    expect(undo(empty)).toBe(empty);
    expect(redo(empty)).toBe(empty);
  });
});

describe('stroke serialization', () => {
  it('round-trips valid strokes', () => {
    const strokes: Stroke[] = [
      { tool: 'pen', size: 4, points: [{ x: 1, y: 2, pressure: 0.3 }] },
      { tool: 'highlighter', size: 22, points: [{ x: 5, y: 6, pressure: 1 }] },
      { tool: 'eraser', size: 30, points: [{ x: 3, y: 4, pressure: 1 }] },
    ];
    expect(parseStrokes(serializeStrokes(strokes))).toEqual(strokes);
  });

  it('returns [] for malformed / empty input', () => {
    expect(parseStrokes(null)).toEqual([]);
    expect(parseStrokes('')).toEqual([]);
    expect(parseStrokes('not json')).toEqual([]);
    expect(parseStrokes('{"not":"array"}')).toEqual([]);
    expect(parseStrokes('[42, null, {"points": "x"}]')).toEqual([]);
  });

  it('drops invalid points and defaults missing tool/size/pressure', () => {
    const parsed = parseStrokes(
      '[{"points":[{"x":1,"y":2},{"x":"bad","y":0}]}]'
    );
    expect(parsed).toEqual([
      { tool: 'pen', size: 4, points: [{ x: 1, y: 2, pressure: 0.5 }] },
    ]);
  });

  it('coerces an unknown tool to pen and non-positive size to the default', () => {
    const parsed = parseStrokes(
      '[{"tool":"crayon","size":-3,"points":[{"x":0,"y":0,"pressure":0.5}]}]'
    );
    expect(parsed[0].tool).toBe('pen');
    expect(parsed[0].size).toBe(4);
  });
});

describe('simplifyStroke', () => {
  const pen = (points: StrokePoint[]): Stroke => ({
    tool: 'pen',
    size: 4,
    points,
  });

  it('rounds coordinates to a tenth of a pixel and pressure to two places', () => {
    const s = simplifyStroke(
      pen([{ x: 1.23456, y: 9.87654, pressure: 0.123456 }])
    );
    expect(s.points).toEqual([{ x: 1.2, y: 9.9, pressure: 0.12 }]);
  });

  it('drops points closer together than the minimum distance', () => {
    const dense = pen([
      { x: 0, y: 0, pressure: 0.5 },
      { x: 0.2, y: 0, pressure: 0.5 },
      { x: 0.4, y: 0, pressure: 0.5 },
      { x: 5, y: 0, pressure: 0.5 },
    ]);
    expect(simplifyStroke(dense).points).toEqual([
      { x: 0, y: 0, pressure: 0.5 },
      { x: 5, y: 0, pressure: 0.5 },
    ]);
  });

  it('always keeps the final point so the stroke ends where it was drawn', () => {
    const s = simplifyStroke(
      pen([
        { x: 0, y: 0, pressure: 0.5 },
        { x: 10, y: 0, pressure: 0.5 },
        { x: 10.3, y: 0, pressure: 0.5 },
      ])
    );
    expect(s.points[s.points.length - 1]).toEqual({
      x: 10.3,
      y: 0,
      pressure: 0.5,
    });
  });

  it('collapses a point that rounding made an exact duplicate', () => {
    const s = simplifyStroke(
      pen([
        { x: 3, y: 3, pressure: 0.5 },
        { x: 3.01, y: 3.01, pressure: 0.5 },
      ])
    );
    expect(s.points).toEqual([{ x: 3, y: 3, pressure: 0.5 }]);
  });

  it('clamps and defaults invalid pressure', () => {
    const s = simplifyStroke(
      pen([
        { x: 0, y: 0, pressure: 5 },
        { x: 20, y: 0, pressure: NaN },
        { x: 40, y: 0, pressure: -2 },
      ])
    );
    expect(s.points.map(p => p.pressure)).toEqual([1, 0.5, 0]);
  });

  it('shrinks a realistic dense stroke dramatically', () => {
    // 2000 sub-pixel samples, as a real pointer firehose produces.
    const points = Array.from({ length: 2000 }, (_, i) => ({
      x: i * 0.05,
      y: Math.sin(i / 50) * 3,
      pressure: 0.5 + Math.sin(i / 7) * 0.001,
    }));
    const before = serializeStrokes([pen(points)]).length;
    const after = serializeStrokes([simplifyStroke(pen(points))]).length;
    expect(after).toBeLessThan(before / 4);
  });

  it('preserves the tool and size', () => {
    const s = simplifyStroke({
      tool: 'highlighter',
      size: 22,
      points: [{ x: 1, y: 1, pressure: 1 }],
    });
    expect(s.tool).toBe('highlighter');
    expect(s.size).toBe(22);
  });
});

describe('eraseStroke', () => {
  const straightLine: Stroke = {
    tool: 'pen',
    size: 4,
    points: [
      { x: 0, y: 0, pressure: 0.5 },
      { x: 10, y: 0, pressure: 0.5 },
      { x: 20, y: 0, pressure: 0.5 },
    ],
  };

  const eraserAcrossMiddle: Stroke = {
    tool: 'eraser',
    size: 8,
    points: [
      { x: 10, y: -10, pressure: 0.5 },
      { x: 10, y: 10, pressure: 0.5 },
    ],
  };

  it('splits a stroke into two pieces when erased through the middle', () => {
    const state = commitStroke(emptyStrokeState(), straightLine);
    const after = eraseStroke(state, eraserAcrossMiddle);
    expect(after.strokes).toHaveLength(2);
    expect(
      after.strokes[0].points[after.strokes[0].points.length - 1].x
    ).toBeLessThan(10);
    expect(after.strokes[1].points[0].x).toBeGreaterThan(10);
  });

  it('removes a stroke entirely when the whole thing is under the eraser', () => {
    const state = commitStroke(emptyStrokeState(), straightLine);
    const after = eraseStroke(
      state,
      {
        tool: 'eraser',
        size: 100,
        points: [
          { x: 0, y: 0, pressure: 0.5 },
          { x: 20, y: 0, pressure: 0.5 },
        ],
      },
      50
    );
    expect(after.strokes).toHaveLength(0);
  });

  it('is undoable as one operation', () => {
    const state = commitStroke(emptyStrokeState(), straightLine);
    const after = eraseStroke(state, eraserAcrossMiddle);
    const back = undo(after);
    expect(back.strokes).toHaveLength(1);
    expect(back.strokes[0].points).toEqual(straightLine.points);
  });

  it('does nothing if the eraser misses', () => {
    const state = commitStroke(emptyStrokeState(), straightLine);
    const after = eraseStroke(state, {
      tool: 'eraser',
      size: 2,
      points: [
        { x: 10, y: 100, pressure: 0.5 },
        { x: 10, y: 110, pressure: 0.5 },
      ],
    });
    expect(after.strokes).toHaveLength(1);
    expect(after.strokes[0].points).toEqual(straightLine.points);
  });
});

describe('resolveSwipe', () => {
  it('next within range advances without creating', () => {
    expect(resolveSwipe('next', 0, 3)).toEqual({ index: 1, createPage: false });
  });

  it('next past the last page signals page creation', () => {
    expect(resolveSwipe('next', 2, 3)).toEqual({ index: 3, createPage: true });
  });

  it('prev moves back and clamps at the first page', () => {
    expect(resolveSwipe('prev', 2, 3)).toEqual({ index: 1, createPage: false });
    expect(resolveSwipe('prev', 0, 3)).toEqual({ index: 0, createPage: false });
  });
});

describe('gesture + width helpers', () => {
  it('recognizes a decisive horizontal swipe only', () => {
    expect(isHorizontalSwipe(80, 10)).toBe(true);
    expect(isHorizontalSwipe(-80, 10)).toBe(true);
    expect(isHorizontalSwipe(30, 10)).toBe(false); // too short
    expect(isHorizontalSwipe(80, 100)).toBe(false); // too vertical
  });

  it('recognizes a tap only when both still and brief', () => {
    expect(isTap(2, 3, 100)).toBe(true);
    expect(isTap(40, 0, 100)).toBe(false); // moved too far
    expect(isTap(2, 3, 900)).toBe(false); // held too long
  });

  it('maps pressure to a clamped width', () => {
    expect(strokeWidth(10, 0)).toBeCloseTo(3.5);
    expect(strokeWidth(10, 1)).toBeCloseTo(10);
    expect(strokeWidth(10, 2)).toBeCloseTo(10); // clamped
    expect(strokeWidth(10, NaN)).toBeCloseTo(6.75); // defaults to 0.5
  });
});
