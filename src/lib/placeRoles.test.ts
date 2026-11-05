import { describe, expect, it } from 'vitest';
import type { PlaceRolePeriod } from '../hooks/api';
import {
  activeOn,
  historyByRole,
  patternsSummary,
  periodRange,
  roleOf,
} from './placeRoles';

const period = (over: Partial<PlaceRolePeriod>): PlaceRolePeriod => ({
  id: 'p',
  role: 'home',
  placeId: 'place',
  placeName: 'Home',
  latitude: 0,
  longitude: 0,
  radiusM: 150,
  startedOn: '2026-01-01',
  endedOn: null,
  ...over,
});

describe('place roles', () => {
  it('treats a period as [start, end)', () => {
    const p = period({ endedOn: '2026-03-01' });
    expect(activeOn(p, '2025-12-31')).toBe(false);
    expect(activeOn(p, '2026-01-01')).toBe(true);
    expect(activeOn(p, '2026-02-28')).toBe(true);
    expect(activeOn(p, '2026-03-01')).toBe(false);
    expect(activeOn(period({}), '2099-01-01')).toBe(true);
  });

  it('finds the role a place holds today, not one it used to', () => {
    const periods = [
      period({ id: 'old', placeId: 'flat', endedOn: '2026-03-01' }),
      period({ id: 'work', placeId: 'office', role: 'work' }),
    ];
    expect(roleOf('flat', periods, '2026-05-01')).toBeUndefined();
    expect(roleOf('flat', periods, '2026-02-01')?.id).toBe('old');
    expect(roleOf('office', periods, '2026-05-01')?.role).toBe('work');
  });

  it('groups history by role, newest first', () => {
    const h = historyByRole([
      period({ id: 'a', startedOn: '2020-01-01', endedOn: '2026-01-01' }),
      period({ id: 'b', startedOn: '2026-01-01' }),
      period({ id: 'w', role: 'work' }),
    ]);
    expect(h.home.map(p => p.id)).toEqual(['b', 'a']);
    expect(h.work.map(p => p.id)).toEqual(['w']);
  });

  it('labels open and closed ranges', () => {
    expect(periodRange(period({}))).toBe('since 2026-01-01');
    expect(periodRange(period({ endedOn: '2026-03-01' }))).toBe(
      '2026-01-01 → 2026-03-01'
    );
  });
});

describe('patternsSummary', () => {
  const stats = (toWork: number | null, toHome: number | null) => ({
    toWork: { count: 1, avgSeconds: toWork },
    toHome: { count: 1, avgSeconds: toHome },
    modes: {},
  });
  it('counts office days of recorded days and averages the commute', () => {
    expect(
      patternsSummary({
        weeks: [],
        weekdays: [],
        commute: stats(1800, 2100),
        observedDays: 30,
        officeDays: 12,
        days: 56,
        since: '2026-05-01',
      })
    ).toBe(
      'Last 8 weeks: at work 12 of 30 recorded days · commute in 30m · home in 35m'
    );
  });
  it('says nothing with nothing recorded', () => {
    expect(patternsSummary(undefined)).toBeNull();
    expect(
      patternsSummary({
        weeks: [],
        weekdays: [],
        commute: stats(null, null),
        observedDays: 0,
        officeDays: 0,
        days: 56,
        since: '',
      })
    ).toBeNull();
  });
});
