import { describe, it, expect } from 'vitest';
import {
  acceptsChar,
  computeStats,
  diffTyped,
  nextRequired,
  previousRequired,
  requiredMask,
} from './practice';

// The positions the writer has to produce, as a string, so a case reads as the
// thing you would actually type rather than as an array of booleans.
function required(target: string): string {
  const mask = requiredMask(target);
  return target
    .split('')
    .filter((_, i) => mask[i])
    .join('');
}

describe('requiredMask', () => {
  it('keeps a space that would otherwise fuse two words', () => {
    expect(required('const item = 1;')).toBe('const item=1;');
  });

  it('drops indentation and line breaks', () => {
    expect(required('if (a) {\n  b();\n}')).toBe('if(a){b();}');
  });

  it('drops spacing around operators, brackets and commas', () => {
    expect(required('useEffect(() => {\n  fetchData();\n}, [id]);')).toBe(
      'useEffect(()=>{fetchData();},[id]);'
    );
  });

  it('asks for one character of a run, not all of it', () => {
    // `const  item` is two spaces in the reference and one keystroke to answer.
    expect(required('const  item')).toBe('const item');
  });

  it('keeps a line break that separates two words', () => {
    // What the 52-character rewrap did to the HTML: an attribute on its own
    // line, where `<inputtype=` is not the same program. One character of the
    // run is required and it is the run's first, here the newline — `acceptsChar`
    // is what lets a space answer it, so this is never a demand to press Enter.
    expect(required('<input\n  type="text"\n/>')).toBe('<input\ntype="text"/>');
  });

  it('drops leading and trailing whitespace', () => {
    expect(required('\n  a;\n')).toBe('a;');
  });
});

describe('acceptsChar', () => {
  it('takes a space for a required line break', () => {
    expect(acceptsChar('<input\n  type', 6, ' ')).toBe(true);
  });

  it('still rejects a wrong character', () => {
    expect(acceptsChar('const item', 5, 'x')).toBe(false);
    expect(acceptsChar('const item', 6, ' ')).toBe(false);
  });
});

describe('nextRequired / previousRequired', () => {
  const mask = requiredMask('a {\n  b\n}');

  it('skips forward over what is filled in', () => {
    // Past `{`, the newline and the indent, landing on `b`.
    expect(nextRequired(mask, 3)).toBe(6);
  });

  it('returns the end when nothing is left to type', () => {
    expect(nextRequired(mask, 9)).toBe(9);
  });

  it('steps a backspace over the filled-in run to the last real character', () => {
    // From `b`, back past the indent and newline to `{`.
    expect(previousRequired(mask, 6)).toBe(2);
  });

  it('lands at the start when there is nothing behind it', () => {
    expect(previousRequired(mask, 0)).toBe(0);
  });
});

describe('diffTyped', () => {
  it('marks untyped characters as pending', () => {
    expect(diffTyped('abc', '')).toEqual(['pending', 'pending', 'pending']);
  });

  it('marks matching characters correct and mismatches incorrect', () => {
    expect(diffTyped('abc', 'axc')).toEqual([
      'correct',
      'incorrect',
      'correct',
    ]);
  });

  it('marks the remaining characters pending once typing is partial', () => {
    expect(diffTyped('abcdef', 'abx')).toEqual([
      'correct',
      'correct',
      'incorrect',
      'pending',
      'pending',
      'pending',
    ]);
  });

  it('is fully correct once the typed text exactly matches', () => {
    expect(diffTyped('abc', 'abc')).toEqual(['correct', 'correct', 'correct']);
  });
});

describe('computeStats', () => {
  it('returns zero wpm and full accuracy with no elapsed time or keystrokes', () => {
    const stats = computeStats({
      targetLength: 10,
      keystrokes: 0,
      mistakes: 0,
      elapsedMs: 0,
    });
    expect(stats.wpm).toBe(0);
    expect(stats.accuracy).toBe(100);
    expect(stats.errorCount).toBe(0);
  });

  it('computes wpm from target length and elapsed time', () => {
    // 10 chars = 2 "words" (chars/5), typed in 30s = 0.5 min -> 4 wpm
    const stats = computeStats({
      targetLength: 10,
      keystrokes: 10,
      mistakes: 0,
      elapsedMs: 30_000,
    });
    expect(stats.wpm).toBeCloseTo(4, 5);
    expect(stats.accuracy).toBe(100);
  });

  it('counts a mistake against accuracy even after it is corrected', () => {
    // Typing "abc" but hitting a wrong key once before backspacing to fix it
    // still costs 4 keystrokes with 1 mistake, not "typed abc correctly".
    const stats = computeStats({
      targetLength: 3,
      keystrokes: 4,
      mistakes: 1,
      elapsedMs: 6_000,
    });
    expect(stats.accuracy).toBeCloseTo(75, 5);
    expect(stats.errorCount).toBe(1);
  });

  it('reports zero wpm when elapsed time is zero but keystrokes happened', () => {
    const stats = computeStats({
      targetLength: 5,
      keystrokes: 5,
      mistakes: 0,
      elapsedMs: 0,
    });
    expect(stats.wpm).toBe(0);
  });
});
