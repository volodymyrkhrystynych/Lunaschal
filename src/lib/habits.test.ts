import { describe, expect, it } from 'vitest';
import { recentDays, streakLabel } from './habits';

describe('recentDays', () => {
  it('lists the week ending today, oldest first', () => {
    const days = recentDays(['2026-10-30', '2026-11-02'], '2026-11-02');
    expect(days.map(d => d.day)).toEqual([
      '2026-10-27',
      '2026-10-28',
      '2026-10-29',
      '2026-10-30',
      '2026-10-31',
      '2026-11-01',
      '2026-11-02',
    ]);
    expect(days.map(d => d.done)).toEqual([
      false,
      false,
      false,
      true,
      false,
      false,
      true,
    ]);
  });

  it('crosses a month and a year', () => {
    expect(recentDays([], '2026-01-01', 3).map(d => d.day)).toEqual([
      '2025-12-30',
      '2025-12-31',
      '2026-01-01',
    ]);
  });

  it('treats missing history as nothing done', () => {
    expect(recentDays(undefined, '2026-11-02').every(d => !d.done)).toBe(true);
  });
});

describe('streakLabel', () => {
  it('shows a running streak only', () => {
    expect(streakLabel(5)).toBe('🔥 5');
    expect(streakLabel(0)).toBeNull();
    expect(streakLabel(undefined)).toBeNull();
  });
});
