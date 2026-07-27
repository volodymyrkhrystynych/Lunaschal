import { describe, it, expect } from 'vitest';
import {
  buildMonthGrid,
  parseEventTags,
  repeatLabel,
  timeSpan,
  toLocalISO,
  weekdayCsvToArray,
} from './calendar';

describe('toLocalISO', () => {
  it('uses the local calendar date, not the UTC one', () => {
    // Late evening local is already tomorrow in UTC for timezones behind UTC
    // (and early morning is still yesterday for those ahead). Deriving the
    // cell's date from toISOString() would shift every event a day.
    expect(toLocalISO(new Date(2026, 6, 9, 23, 30))).toBe('2026-07-09');
    expect(toLocalISO(new Date(2026, 6, 9, 0, 30))).toBe('2026-07-09');
  });

  it('zero-pads month and day', () => {
    expect(toLocalISO(new Date(2026, 0, 5))).toBe('2026-01-05');
  });
});

describe('buildMonthGrid', () => {
  it('always returns 42 cells regardless of month shape', () => {
    // Feb 2026 starts on a Sunday and has 28 days — a natural 4-row month.
    expect(buildMonthGrid(2026, 1)).toHaveLength(42);
    // Aug 2026 starts on a Saturday — a natural 6-row month.
    expect(buildMonthGrid(2026, 7)).toHaveLength(42);
  });

  it('starts on the Sunday on or before the 1st', () => {
    // July 2026 starts on a Wednesday, so the grid opens on Sun Jun 28.
    const grid = buildMonthGrid(2026, 6);
    expect(grid[0].iso).toBe('2026-06-28');
    expect(grid[0].date.getDay()).toBe(0);
    expect(grid[0].inMonth).toBe(false);
  });

  it('flags in-month days and pads the rest', () => {
    const grid = buildMonthGrid(2026, 6); // July 2026, 31 days
    expect(grid.filter(c => c.inMonth)).toHaveLength(31);
    expect(grid.find(c => c.iso === '2026-07-01')!.inMonth).toBe(true);
    expect(grid.find(c => c.iso === '2026-08-01')!.inMonth).toBe(false);
  });

  it('runs consecutively with no gaps or repeats', () => {
    const grid = buildMonthGrid(2026, 1);
    expect(new Set(grid.map(c => c.iso)).size).toBe(42);
    for (let i = 1; i < grid.length; i++) {
      const gap =
        (grid[i].date.getTime() - grid[i - 1].date.getTime()) / 86_400_000;
      expect(Math.round(gap)).toBe(1);
    }
  });

  it('handles a month whose grid crosses a year boundary', () => {
    const grid = buildMonthGrid(2026, 11); // December 2026
    expect(grid[0].iso.startsWith('2026-11')).toBe(true);
    expect(grid[41].iso.startsWith('2027-01')).toBe(true);
    expect(grid.filter(c => c.inMonth)).toHaveLength(31);
  });
});

describe('weekdayCsvToArray', () => {
  it('parses, sorts and filters', () => {
    expect(weekdayCsvToArray('5,1,3')).toEqual([1, 3, 5]);
    expect(weekdayCsvToArray('1, 2 ,3')).toEqual([1, 2, 3]);
    expect(weekdayCsvToArray('7,-1,x,2')).toEqual([2]);
    expect(weekdayCsvToArray(null)).toEqual([]);
    expect(weekdayCsvToArray('')).toEqual([]);
  });
});

describe('repeatLabel', () => {
  it('is empty for a one-off', () => {
    expect(repeatLabel({ repeatFreq: null })).toBe('');
  });

  it('names the common weekday set', () => {
    expect(
      repeatLabel({
        repeatFreq: 'weekly',
        repeatInterval: 1,
        repeatByweekday: '1,2,3,4,5',
      })
    ).toBe('weekdays');
  });

  it('lists an arbitrary weekday set', () => {
    expect(
      repeatLabel({
        repeatFreq: 'weekly',
        repeatInterval: 1,
        repeatByweekday: '1,3',
      })
    ).toBe('Mon, Wed');
  });

  it('describes an interval on top of a weekday set', () => {
    expect(
      repeatLabel({
        repeatFreq: 'weekly',
        repeatInterval: 2,
        repeatByweekday: '5',
      })
    ).toBe('every 2 weeks on Fri');
  });

  it('handles daily and monthly', () => {
    expect(repeatLabel({ repeatFreq: 'daily', repeatInterval: 1 })).toBe(
      'every day'
    );
    expect(repeatLabel({ repeatFreq: 'daily', repeatInterval: 3 })).toBe(
      'every 3 days'
    );
    expect(repeatLabel({ repeatFreq: 'monthly', repeatInterval: 1 })).toBe(
      'every month'
    );
  });

  it('falls back to the plain interval when no weekdays are set', () => {
    expect(repeatLabel({ repeatFreq: 'weekly', repeatInterval: 1 })).toBe(
      'every week'
    );
  });
});

describe('parseEventTags', () => {
  it('parses a JSON array', () => {
    expect(parseEventTags('["work","admin"]')).toEqual(['work', 'admin']);
  });

  it('degrades to no tags rather than throwing', () => {
    // A throw here would take down the whole calendar render.
    expect(parseEventTags('not json')).toEqual([]);
    expect(parseEventTags('{"a":1}')).toEqual([]);
    expect(parseEventTags(null)).toEqual([]);
    expect(parseEventTags('')).toEqual([]);
  });

  it('drops non-string members', () => {
    expect(parseEventTags('["work",3,null]')).toEqual(['work']);
  });
});

describe('timeSpan', () => {
  it('renders a start/end pair, a bare start, or nothing', () => {
    expect(timeSpan('09:00', '17:00')).toBe('09:00–17:00');
    expect(timeSpan('09:00', null)).toBe('09:00');
    expect(timeSpan(null, '17:00')).toBe('');
  });
});
