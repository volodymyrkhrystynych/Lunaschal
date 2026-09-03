import { beforeEach, describe, expect, it } from 'vitest';
import {
  commitStroke,
  DEFAULT_PANEL_PLACEMENT,
  DEFAULT_STROKE_SIZE,
  emptyStrokeState,
  eraseStroke,
  fitPageBox,
  isHorizontalSwipe,
  isPageSpace,
  isTap,
  loadPanelPlacement,
  PAGE_ASPECT,
  PAGE_HEIGHT,
  PAGE_WIDTH,
  PANEL_PLACEMENT_KEY,
  panelOrientation,
  panelPosition,
  parseBuffer,
  parsePanelPlacement,
  parseStrokes,
  redo,
  resolveSnapEdge,
  resolveSwipe,
  savePanelPlacement,
  saveStatusLabel,
  serializeBuffer,
  serializeStrokes,
  simplifyStroke,
  sizeDotPx,
  snapPlacement,
  strokeWidth,
  toPageSpace,
  toPageSpaceStrokes,
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
      {
        tool: 'pen',
        size: DEFAULT_STROKE_SIZE,
        points: [{ x: 1, y: 2, pressure: 0.5 }],
      },
    ]);
  });

  it('coerces an unknown tool to pen and non-positive size to the default', () => {
    const parsed = parseStrokes(
      '[{"tool":"crayon","size":-3,"points":[{"x":0,"y":0,"pressure":0.5}]}]'
    );
    expect(parsed[0].tool).toBe('pen');
    expect(parsed[0].size).toBe(DEFAULT_STROKE_SIZE);
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

  it('previews stroke widths as dots that fit the button', () => {
    expect(sizeDotPx(4)).toBe(2);
    expect(sizeDotPx(14)).toBe(7);
    expect(sizeDotPx(100)).toBe(18); // capped
  });
});

describe('fitPageBox', () => {
  it('is always A4 portrait, whatever the viewport', () => {
    for (const viewport of [
      { width: 1600, height: 900 },
      { width: 820, height: 1180 },
      { width: 300, height: 300 },
    ]) {
      const box = fitPageBox(viewport);
      expect(box.width / box.height).toBeCloseTo(PAGE_ASPECT, 6);
    }
  });

  it('fits a wide viewport by height, with bars left and right', () => {
    const box = fitPageBox({ width: 1600, height: 900 });
    expect(box.height).toBe(900);
    expect(box.width).toBeCloseTo(900 * PAGE_ASPECT);
    expect(box.top).toBe(0);
    expect(box.left).toBeCloseTo((1600 - box.width) / 2);
    expect(box.left).toBeGreaterThan(0);
  });

  it('fits a tall viewport by width, with bars top and bottom', () => {
    const box = fitPageBox({ width: 800, height: 2000 });
    expect(box.width).toBe(800);
    expect(box.height).toBeCloseTo(800 / PAGE_ASPECT);
    expect(box.left).toBe(0);
    expect(box.top).toBeCloseTo((2000 - box.height) / 2);
  });

  it('never crops: the page always fits inside the viewport', () => {
    for (const viewport of [
      { width: 1024, height: 200 },
      { width: 200, height: 1024 },
      { width: 500, height: 707 },
    ]) {
      const box = fitPageBox(viewport);
      expect(box.width).toBeLessThanOrEqual(viewport.width + 1e-9);
      expect(box.height).toBeLessThanOrEqual(viewport.height + 1e-9);
    }
  });

  it('degrades to an empty box for a viewport that has not been measured', () => {
    expect(fitPageBox({ width: 0, height: 0 })).toEqual({
      width: 0,
      height: 0,
      left: 0,
      top: 0,
      scale: 0,
    });
    expect(fitPageBox({ width: NaN, height: 500 }).width).toBe(0);
  });
});

describe('toPageSpace', () => {
  it('maps the corners of the on-screen page onto the page corners', () => {
    const box = fitPageBox({ width: 1600, height: 900 });
    expect(toPageSpace(0, 0, box)).toEqual({ x: 0, y: 0 });
    const br = toPageSpace(box.width, box.height, box);
    expect(br.x).toBeCloseTo(PAGE_WIDTH);
    expect(br.y).toBeCloseTo(PAGE_HEIGHT);
  });

  it('is resolution-independent: the same touch lands on the same page point', () => {
    // The bug this replaces: a stroke drawn on a desktop-shaped page came back
    // squashed on an iPad, because the stored space was the screen box.
    const desktop = fitPageBox({ width: 1600, height: 900 });
    const ipad = fitPageBox({ width: 820, height: 1180 });
    // Same relative spot on the sheet, two very different fitted sizes.
    const a = toPageSpace(desktop.width * 0.25, desktop.height * 0.6, desktop);
    const b = toPageSpace(ipad.width * 0.25, ipad.height * 0.6, ipad);
    expect(a.x).toBeCloseTo(b.x, 6);
    expect(a.y).toBeCloseTo(b.y, 6);
  });

  it('survives an unmeasured box without producing NaN', () => {
    expect(toPageSpace(10, 10, { width: 0, height: 0 })).toEqual({
      x: 0,
      y: 0,
    });
  });
});

describe('legacy stroke conversion', () => {
  const legacy: Stroke[] = [
    {
      tool: 'pen',
      size: 4,
      points: [
        { x: 0, y: 0, pressure: 0.5 },
        { x: 1000, y: 700, pressure: 0.5 },
      ],
    },
  ];

  it('recognizes page-space rows (and an unsaved page) as needing nothing', () => {
    expect(isPageSpace({ width: PAGE_WIDTH, height: PAGE_HEIGHT })).toBe(true);
    expect(isPageSpace(null)).toBe(true);
    expect(isPageSpace({ width: 1000, height: 700 })).toBe(false);
  });

  it('returns page-space strokes untouched', () => {
    expect(
      toPageSpaceStrokes(legacy, { width: PAGE_WIDTH, height: PAGE_HEIGHT })
    ).toBe(legacy);
    expect(toPageSpaceStrokes(legacy, null)).toBe(legacy);
  });

  it('scales an old screen-space drawing onto the page without distorting it', () => {
    const [s] = toPageSpaceStrokes(legacy, { width: 1000, height: 700 });
    // Uniform contain fit: 1000x700 into 2100x2970 scales by 2.1 on both axes.
    const drawnAspect =
      (s.points[1].x - s.points[0].x) / (s.points[1].y - s.points[0].y);
    expect(drawnAspect).toBeCloseTo(1000 / 700, 6);
    expect(s.size).toBeCloseTo(4 * 2.1);
  });

  it('centres the converted drawing on the page', () => {
    const [s] = toPageSpaceStrokes(legacy, { width: 1000, height: 700 });
    const topLeft = s.points[0];
    const bottomRight = s.points[1];
    // The 1000x700 sheet is width-limited, so it is centred vertically.
    expect(topLeft.x).toBeCloseTo(0);
    expect(bottomRight.x).toBeCloseTo(PAGE_WIDTH);
    expect(topLeft.y).toBeCloseTo(PAGE_HEIGHT / 2 - (700 * 2.1) / 2);
    expect(bottomRight.y + topLeft.y).toBeCloseTo(PAGE_HEIGHT);
  });

  it('keeps every converted point on the page', () => {
    for (const source of [
      { width: 1600, height: 400 },
      { width: 400, height: 1600 },
      { width: 1024, height: 768 },
    ]) {
      const corners: Stroke[] = [
        {
          tool: 'pen',
          size: 4,
          points: [
            { x: 0, y: 0, pressure: 0.5 },
            { x: source.width, y: source.height, pressure: 0.5 },
          ],
        },
      ];
      for (const p of toPageSpaceStrokes(corners, source)[0].points) {
        expect(p.x).toBeGreaterThanOrEqual(-1e-9);
        expect(p.x).toBeLessThanOrEqual(PAGE_WIDTH + 1e-9);
        expect(p.y).toBeGreaterThanOrEqual(-1e-9);
        expect(p.y).toBeLessThanOrEqual(PAGE_HEIGHT + 1e-9);
      }
    }
  });
});

describe('local buffer', () => {
  const strokes: Stroke[] = [
    { tool: 'pen', size: 8, points: [{ x: 10, y: 20, pressure: 0.5 }] },
  ];

  it('round-trips page-space strokes', () => {
    expect(parseBuffer(serializeBuffer(strokes), null)).toEqual(strokes);
  });

  it('converts a buffer written before the page space instead of dropping it', () => {
    // The old format was a bare stroke array in the page's screen-sized space.
    const old = serializeStrokes([
      { tool: 'pen', size: 4, points: [{ x: 500, y: 350, pressure: 0.5 }] },
    ]);
    const parsed = parseBuffer(old, { width: 1000, height: 700 });
    expect(parsed).toEqual([
      {
        tool: 'pen',
        size: 4 * 2.1,
        points: [{ x: PAGE_WIDTH / 2, y: PAGE_HEIGHT / 2, pressure: 0.5 }],
      },
    ]);
  });

  it('returns null when there is nothing usable held locally', () => {
    expect(parseBuffer(undefined, null)).toBeNull();
    expect(parseBuffer('', null)).toBeNull();
    expect(parseBuffer('not json', null)).toBeNull();
    expect(parseBuffer('{"space":"other"}', null)).toBeNull();
  });

  it('tells an empty buffered page apart from no buffer at all', () => {
    expect(parseBuffer(serializeBuffer([]), null)).toEqual([]);
    expect(parseBuffer(null, null)).toBeNull();
  });
});

describe('tool panel placement', () => {
  it('snaps to whichever edge the panel was dropped nearest', () => {
    const bounds = { width: 1000, height: 800 };
    expect(resolveSnapEdge({ x: 500, y: 20 }, bounds)).toBe('top');
    expect(resolveSnapEdge({ x: 500, y: 780 }, bounds)).toBe('bottom');
    expect(resolveSnapEdge({ x: 30, y: 400 }, bounds)).toBe('left');
    expect(resolveSnapEdge({ x: 970, y: 400 }, bounds)).toBe('right');
    // Dead centre of a landscape area: the top and bottom are nearest.
    expect(resolveSnapEdge({ x: 500, y: 400 }, bounds)).toBe('top');
  });

  it('remembers how far along the edge it was dropped, as a fraction', () => {
    const bounds = { width: 1000, height: 800 };
    expect(snapPlacement({ x: 250, y: 10 }, bounds)).toEqual({
      edge: 'top',
      offset: 0.25,
    });
    expect(snapPlacement({ x: 10, y: 600 }, bounds)).toEqual({
      edge: 'left',
      offset: 0.75,
    });
  });

  it('clamps a drop outside the area and survives an unmeasured one', () => {
    // Dragged off past the top-left corner — still lands on a real edge.
    expect(
      snapPlacement({ x: -50, y: -80 }, { width: 100, height: 400 })
    ).toEqual({ edge: 'top', offset: 0 });
    expect(
      snapPlacement({ x: 900, y: 200 }, { width: 100, height: 400 })
    ).toEqual({ edge: 'right', offset: 0.5 });
    expect(snapPlacement({ x: 5, y: 5 }, { width: 0, height: 0 }).offset).toBe(
      0.5
    );
  });

  it('lies along the edge it is docked to', () => {
    expect(panelOrientation('top')).toBe('horizontal');
    expect(panelOrientation('bottom')).toBe('horizontal');
    expect(panelOrientation('left')).toBe('vertical');
    expect(panelOrientation('right')).toBe('vertical');
  });

  it('positions the panel against its edge, centred on the stored offset', () => {
    const bounds = { width: 1000, height: 800 };
    const horizontal = { width: 400, height: 56 };
    const vertical = { width: 56, height: 400 };
    expect(
      panelPosition({ edge: 'top', offset: 0.5 }, bounds, horizontal)
    ).toEqual({ left: 300, top: 12 });
    expect(
      panelPosition({ edge: 'bottom', offset: 0.5 }, bounds, horizontal)
    ).toEqual({ left: 300, top: 800 - 56 - 12 });
    expect(
      panelPosition({ edge: 'left', offset: 0.5 }, bounds, vertical)
    ).toEqual({ left: 12, top: 200 });
    expect(
      panelPosition({ edge: 'right', offset: 0.5 }, bounds, vertical)
    ).toEqual({ left: 1000 - 56 - 12, top: 200 });
  });

  it('keeps the panel fully on screen at the extremes', () => {
    const bounds = { width: 1000, height: 800 };
    const panel = { width: 400, height: 56 };
    const atStart = panelPosition({ edge: 'top', offset: 0 }, bounds, panel);
    expect(atStart.left).toBe(12);
    const atEnd = panelPosition({ edge: 'top', offset: 1 }, bounds, panel);
    expect(atEnd.left).toBe(1000 - 400 - 12);
  });

  it('does not go negative when the panel is wider than the area', () => {
    const pos = panelPosition(
      { edge: 'top', offset: 0.5 },
      { width: 200, height: 800 },
      { width: 400, height: 56 }
    );
    expect(pos.left).toBe(12);
  });

  it('parses a stored placement and rejects a broken one', () => {
    expect(parsePanelPlacement('{"edge":"right","offset":0.2}')).toEqual({
      edge: 'right',
      offset: 0.2,
    });
    expect(parsePanelPlacement('{"edge":"right"}')).toEqual({
      edge: 'right',
      offset: 0.5,
    });
    expect(parsePanelPlacement('{"edge":"middle","offset":0.2}')).toBeNull();
    expect(parsePanelPlacement('nonsense')).toBeNull();
    expect(parsePanelPlacement(null)).toBeNull();
  });
});

describe('panel placement persistence', () => {
  // A minimal localStorage stand-in: these tests run in the node environment.
  beforeEach(() => {
    const store = new Map<string, string>();
    (globalThis as { localStorage?: unknown }).localStorage = {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
      removeItem: (k: string) => void store.delete(k),
    };
  });

  it('round-trips through storage', () => {
    savePanelPlacement({ edge: 'bottom', offset: 0.3 });
    expect(localStorage.getItem(PANEL_PLACEMENT_KEY)).toBeTruthy();
    expect(loadPanelPlacement()).toEqual({ edge: 'bottom', offset: 0.3 });
  });

  it('falls back to the default when nothing (or nonsense) is stored', () => {
    expect(loadPanelPlacement()).toEqual(DEFAULT_PANEL_PLACEMENT);
    localStorage.setItem(PANEL_PLACEMENT_KEY, 'broken');
    expect(loadPanelPlacement()).toEqual(DEFAULT_PANEL_PLACEMENT);
  });

  it('shrugs off storage that throws (private mode)', () => {
    (globalThis as { localStorage?: unknown }).localStorage = {
      getItem: () => {
        throw new Error('denied');
      },
      setItem: () => {
        throw new Error('denied');
      },
    };
    expect(loadPanelPlacement()).toEqual(DEFAULT_PANEL_PLACEMENT);
    expect(() => savePanelPlacement({ edge: 'top', offset: 0 })).not.toThrow();
  });
});

describe('saveStatusLabel', () => {
  it('reports the three states in a slot that never changes size', () => {
    expect(saveStatusLabel(true, 1)).toBe('Saving…');
    expect(saveStatusLabel(false, 1)).toBe('Unsaved');
    expect(saveStatusLabel(false, 0)).toBe('Saved');
    // Always some text: an empty label is what let the old bar reflow.
    for (const label of [
      saveStatusLabel(true, 0),
      saveStatusLabel(false, 1),
      saveStatusLabel(false, 0),
    ]) {
      expect(label.length).toBeGreaterThan(0);
    }
  });

  it('counts the pages waiting, not just the one on screen', () => {
    // Save sends the whole paper, so a page flipped away from is still
    // unsaved — a status that only knew about the open page said "Saved".
    expect(saveStatusLabel(false, 3)).toBe('3 unsaved');
    expect(saveStatusLabel(false, 2)).toBe('2 unsaved');
    // Saving wins over the count: it is the state that is about to change.
    expect(saveStatusLabel(true, 3)).toBe('Saving…');
  });
});
