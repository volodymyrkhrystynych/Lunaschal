import { describe, expect, it } from 'vitest';
import {
  colorsFor,
  HIGHLIGHTER_COLORS,
  parseStrokes,
  PEN_COLORS,
  serializeStrokes,
  sizeDotPx,
  strokeColor,
  type InkPalette,
  type Stroke,
} from './ink';

const PAPER: InkPalette = {
  ink: '#111111',
  highlight: '#ffe14d',
  highlightAlpha: 0.4,
};
const NEWS: InkPalette = {
  ink: '#1756ad',
  highlight: '#ffdb00',
  highlightAlpha: 0.35,
};

const stroke = (over: Partial<Stroke> = {}): Stroke => ({
  tool: 'pen',
  size: 8,
  points: [{ x: 1, y: 2, pressure: 0.5 }],
  ...over,
});

describe('stroke colour', () => {
  // The whole reason colour is optional rather than defaulted at parse time:
  // Paper's ink has always been black and the newspaper's has always been
  // blue, and neither has a colour stored on a single existing stroke. A
  // parse-time default would have to pick one and silently restyle the other.
  it('falls back to whatever the surface calls ink', () => {
    expect(strokeColor(stroke(), PAPER)).toBe('#111111');
    expect(strokeColor(stroke(), NEWS)).toBe('#1756ad');
  });

  it('falls back per tool, so a highlighter is never ink-coloured', () => {
    expect(strokeColor(stroke({ tool: 'highlighter' }), PAPER)).toBe('#ffe14d');
    expect(strokeColor(stroke({ tool: 'highlighter' }), NEWS)).toBe('#ffdb00');
  });

  it('lets a stroke that chose a colour keep it on any surface', () => {
    const chosen = stroke({ color: '#c0392b' });
    expect(strokeColor(chosen, PAPER)).toBe('#c0392b');
    expect(strokeColor(chosen, NEWS)).toBe('#c0392b');
  });

  it('survives a save and reload, and stays absent when it was never set', () => {
    const [withColor, without] = parseStrokes(
      serializeStrokes([stroke({ color: '#1e8449' }), stroke()])
    );
    expect(withColor.color).toBe('#1e8449');
    expect(without.color).toBeUndefined();
    expect(strokeColor(without, PAPER)).toBe('#111111');
  });

  it('ignores a colour that is not a string rather than storing junk', () => {
    const [parsed] = parseStrokes(
      JSON.stringify([{ ...stroke(), color: { r: 1 } }])
    );
    expect(parsed.color).toBeUndefined();
  });
});

describe('the palette a tool offers', () => {
  it('gives the pen and the highlighter different swatches', () => {
    expect(colorsFor('pen')).toEqual(PEN_COLORS);
    expect(colorsFor('highlighter')).toEqual(HIGHLIGHTER_COLORS);
  });

  // The eraser removes ink rather than laying any down.
  it('gives the eraser none', () => {
    expect(colorsFor('eraser')).toEqual([]);
  });
});

describe('width preview dots', () => {
  // Paper's units are tenths of a millimetre, about two per CSS pixel.
  it('keeps the A4 page scale it has always used', () => {
    expect(sizeDotPx(4)).toBe(2);
    expect(sizeDotPx(14)).toBe(7);
    expect(sizeDotPx(100)).toBe(18); // capped
  });

  // The newspaper's units are thousandths of a page width, so a size-2 pen
  // would preview as a 1px speck on the shared panel.
  it('scales to a surface whose units are much smaller, with a floor', () => {
    expect(sizeDotPx(2, 0.35, 6)).toBeGreaterThanOrEqual(6);
    expect(sizeDotPx(24, 0.35, 6)).toBe(18); // still capped
  });
});
