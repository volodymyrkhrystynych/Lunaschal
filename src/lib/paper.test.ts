import { describe, expect, it } from 'vitest';
import {
  commitStroke,
  emptyStrokeState,
  isHorizontalSwipe,
  parseStrokes,
  redo,
  resolveSwipe,
  serializeStrokes,
  strokeWidth,
  undo,
  type Stroke,
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

  it('undo moves the last stroke to the redo stack; redo restores it', () => {
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

  it('maps pressure to a clamped width', () => {
    expect(strokeWidth(10, 0)).toBeCloseTo(3.5);
    expect(strokeWidth(10, 1)).toBeCloseTo(10);
    expect(strokeWidth(10, 2)).toBeCloseTo(10); // clamped
    expect(strokeWidth(10, NaN)).toBeCloseTo(6.75); // defaults to 0.5
  });
});
