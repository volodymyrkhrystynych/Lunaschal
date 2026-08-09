import { describe, it, expect } from 'vitest';
import { diffTyped, computeStats } from './practice';

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
