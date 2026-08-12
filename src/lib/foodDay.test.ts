import { describe, it, expect } from 'vitest';
import { foodDayKey, foodDayLabel, groupByFoodDay } from './foodDay';

describe('foodDayKey', () => {
  it('keeps a mid-afternoon timestamp on its calendar day', () => {
    expect(foodDayKey('2026-08-12T15:30:00')).toBe('2026-08-12');
  });

  it('rolls a timestamp just after midnight back to the previous day', () => {
    expect(foodDayKey('2026-08-12T02:30:00')).toBe('2026-08-11');
  });

  it('rolls a timestamp right at 3:59am back to the previous day', () => {
    expect(foodDayKey('2026-08-12T03:59:59')).toBe('2026-08-11');
  });

  it('counts 4:00am exactly as the start of the new day', () => {
    expect(foodDayKey('2026-08-12T04:00:00')).toBe('2026-08-12');
  });
});

describe('foodDayLabel', () => {
  it('formats a day key as a short weekday/month/day label', () => {
    expect(foodDayLabel('2026-08-12')).toBe('Wed, Aug 12');
  });
});

describe('groupByFoodDay', () => {
  it('merges consecutive entries sharing a 4am day', () => {
    const entries = [
      { id: 'a', createdAt: '2026-08-12T20:00:00' },
      { id: 'b', createdAt: '2026-08-12T12:00:00' },
      { id: 'c', createdAt: '2026-08-12T02:00:00' }, // rolls into Aug 11
      { id: 'd', createdAt: '2026-08-11T18:00:00' },
    ];

    const groups = groupByFoodDay(entries);

    expect(groups.map(g => g.dayKey)).toEqual(['2026-08-12', '2026-08-11']);
    expect(groups[0].items.map(e => e.id)).toEqual(['a', 'b']);
    expect(groups[1].items.map(e => e.id)).toEqual(['c', 'd']);
  });

  it('does not merge non-consecutive entries from the same day', () => {
    const entries = [
      { id: 'a', createdAt: '2026-08-12T20:00:00' },
      { id: 'b', createdAt: '2026-08-11T12:00:00' },
      { id: 'c', createdAt: '2026-08-12T08:00:00' },
    ];

    const groups = groupByFoodDay(entries);

    expect(groups.map(g => g.dayKey)).toEqual([
      '2026-08-12',
      '2026-08-11',
      '2026-08-12',
    ]);
  });

  it('returns an empty array for no entries', () => {
    expect(groupByFoodDay([])).toEqual([]);
  });
});
