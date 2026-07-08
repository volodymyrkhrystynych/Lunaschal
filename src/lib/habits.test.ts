import { describe, expect, it } from 'vitest';

import { cellLevel, gridWeeks, isScheduled, mondayWeekday, nextBooleanState, parseISODate } from './habits';

// Anchors: 2026-07-08 is a Wednesday; 2026-07-06 is a Monday.

describe('gridWeeks', () => {
  it('returns numWeeks columns of 7 dates each', () => {
    const weeks = gridWeeks('2026-07-08', 20);
    expect(weeks).toHaveLength(20);
    for (const col of weeks) expect(col).toHaveLength(7);
  });

  it('last column is the Monday-start week containing today', () => {
    const weeks = gridWeeks('2026-07-08', 4);
    const last = weeks[weeks.length - 1];
    expect(last[0]).toBe('2026-07-06'); // Monday
    expect(last).toContain('2026-07-08');
    expect(last[6]).toBe('2026-07-12'); // Sunday (future, rendered disabled)
  });

  it('columns are consecutive weeks', () => {
    const weeks = gridWeeks('2026-07-08', 3);
    expect(weeks[0][0]).toBe('2026-06-22');
    expect(weeks[1][0]).toBe('2026-06-29');
    expect(weeks[2][0]).toBe('2026-07-06');
  });

  it('works across a year boundary', () => {
    const weeks = gridWeeks('2026-01-02', 2); // Friday; week starts Mon 2025-12-29
    expect(weeks[1][0]).toBe('2025-12-29');
    expect(weeks[0][0]).toBe('2025-12-22');
    expect(weeks[1]).toContain('2026-01-01');
  });

  it('today on a Monday still lands in the last column', () => {
    const weeks = gridWeeks('2026-07-06', 2);
    expect(weeks[1][0]).toBe('2026-07-06');
  });
});

describe('mondayWeekday', () => {
  it('maps Monday to 0 and Sunday to 6', () => {
    expect(mondayWeekday(parseISODate('2026-07-06'))).toBe(0); // Monday
    expect(mondayWeekday(parseISODate('2026-07-08'))).toBe(2); // Wednesday
    expect(mondayWeekday(parseISODate('2026-07-12'))).toBe(6); // Sunday
  });
});

describe('isScheduled', () => {
  it('daily and per_week are always scheduled', () => {
    expect(isScheduled('2026-07-08', { scheduleType: 'daily', scheduleDays: null })).toBe(true);
    expect(isScheduled('2026-07-08', { scheduleType: 'per_week', scheduleDays: null })).toBe(true);
  });

  it('weekdays respects the Mon-based day list including Sunday', () => {
    const mwf = { scheduleType: 'weekdays' as const, scheduleDays: [0, 2, 4] };
    expect(isScheduled('2026-07-06', mwf)).toBe(true); // Mon
    expect(isScheduled('2026-07-07', mwf)).toBe(false); // Tue
    expect(isScheduled('2026-07-08', mwf)).toBe(true); // Wed
    const sun = { scheduleType: 'weekdays' as const, scheduleDays: [6] };
    expect(isScheduled('2026-07-12', sun)).toBe(true); // Sunday
    expect(isScheduled('2026-07-06', sun)).toBe(false);
  });
});

describe('cellLevel', () => {
  const bool = { type: 'boolean' as const, targetValue: null };
  const qty = { type: 'quantity' as const, targetValue: 10 };

  it('empty cell is 0, boolean done is max', () => {
    expect(cellLevel(undefined, bool)).toBe(0);
    expect(cellLevel({ status: 'done', value: null }, bool)).toBe(4);
  });

  it('skipped passes through', () => {
    expect(cellLevel({ status: 'skipped', value: null }, bool)).toBe('skipped');
    expect(cellLevel({ status: 'skipped', value: 5 }, qty)).toBe('skipped');
  });

  it('quantity buckets by progress toward target', () => {
    expect(cellLevel({ status: 'done', value: 0 }, qty)).toBe(0);
    expect(cellLevel({ status: 'done', value: 3 }, qty)).toBe(1);
    expect(cellLevel({ status: 'done', value: 5 }, qty)).toBe(2);
    expect(cellLevel({ status: 'done', value: 9 }, qty)).toBe(3);
    expect(cellLevel({ status: 'done', value: 10 }, qty)).toBe(4);
    expect(cellLevel({ status: 'done', value: 15 }, qty)).toBe(4);
  });
});

describe('nextBooleanState', () => {
  it('cycles none -> done -> skipped -> none', () => {
    expect(nextBooleanState('none')).toBe('done');
    expect(nextBooleanState('done')).toBe('skipped');
    expect(nextBooleanState('skipped')).toBe('none');
  });
});
