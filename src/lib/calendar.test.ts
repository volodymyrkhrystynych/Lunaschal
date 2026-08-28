import { describe, it, expect } from 'vitest';
import {
  buildMonthGrid,
  parseEventTags,
  eventTimeLabel,
  repeatDraftFromEvent,
  repeatDraftToPayload,
  repeatLabel,
  repeatUnitLabel,
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

describe('repeatLabel yearly', () => {
  it('names the year unit', () => {
    expect(repeatLabel({ repeatFreq: 'yearly', repeatInterval: 1 })).toBe(
      'every year'
    );
    expect(repeatLabel({ repeatFreq: 'yearly', repeatInterval: 2 })).toBe(
      'every 2 years'
    );
  });
});

describe('eventTimeLabel', () => {
  it('reads all-day events as all day', () => {
    expect(eventTimeLabel({ allDay: true, time: null })).toBe('All day');
  });

  it('lets the flag win over a leftover time', () => {
    expect(eventTimeLabel({ allDay: true, time: '09:00' })).toBe('All day');
  });

  it('accepts the 0/1 SQLite sends for the flag', () => {
    expect(eventTimeLabel({ allDay: 1, time: null })).toBe('All day');
    expect(eventTimeLabel({ allDay: 0, time: '09:00' })).toBe('09:00');
  });

  it('leaves a merely untimed event blank, as it always rendered', () => {
    expect(eventTimeLabel({ allDay: false, time: null })).toBe('');
  });

  it('still spans start and end for a timed event', () => {
    expect(eventTimeLabel({ time: '09:00', endTime: '17:00' })).toBe(
      '09:00\u201317:00'
    );
  });
});

describe('repeatUnitLabel', () => {
  it('counts years for a yearly rule', () => {
    // The interval box used to read "month(s)" here, so a birthday set to
    // repeat every year looked like it repeated every N months.
    expect(repeatUnitLabel('yearly')).toBe('year(s)');
  });

  it('counts the matching unit for the other frequencies', () => {
    expect(repeatUnitLabel('daily')).toBe('day(s)');
    expect(repeatUnitLabel('weekly')).toBe('week(s)');
    expect(repeatUnitLabel('monthly')).toBe('month(s)');
  });
});

describe('repeatDraftFromEvent', () => {
  it('reads a stored weekly rule back into the form shape', () => {
    expect(
      repeatDraftFromEvent({
        repeatFreq: 'weekly',
        repeatInterval: 2,
        repeatByweekday: '1,3,5',
        repeatUntil: '2026-12-31',
      })
    ).toEqual({
      freq: 'weekly',
      interval: 2,
      byweekday: [1, 3, 5],
      until: '2026-12-31',
    });
  });

  it('turns a one-off into an empty draft rather than a half-filled one', () => {
    expect(
      repeatDraftFromEvent({
        repeatFreq: null,
        repeatInterval: null,
        repeatByweekday: null,
        repeatUntil: null,
      })
    ).toEqual({ freq: '', interval: 1, byweekday: [], until: '' });
  });

  it('falls back to an interval of 1 for a rule stored without one', () => {
    // Rows predating repeat_interval carry NULL, and a 0 in the box is a rule
    // the backend rejects.
    expect(
      repeatDraftFromEvent({ repeatFreq: 'monthly', repeatInterval: null })
        .interval
    ).toBe(1);
  });

  it('drops a frequency outside the closed vocabulary', () => {
    expect(repeatDraftFromEvent({ repeatFreq: 'hourly' }).freq).toBe('');
  });
});

describe('repeatDraftToPayload', () => {
  it('clears every parameter when the rule is turned off', () => {
    // Explicit nulls, not omissions: `repeatFreq: null` is what turns a series
    // back into a one-off, and leaving the parameters behind would let a later
    // re-enable inherit stale weekdays.
    expect(
      repeatDraftToPayload({
        freq: '',
        interval: 3,
        byweekday: [1, 2],
        until: '2026-12-31',
      })
    ).toEqual({
      repeatFreq: null,
      repeatInterval: null,
      repeatByweekday: null,
      repeatUntil: null,
    });
  });

  it('sends the weekdays only for a weekly rule', () => {
    const draft = { interval: 1, byweekday: [1, 3], until: '' };
    expect(
      repeatDraftToPayload({ ...draft, freq: 'weekly' }).repeatByweekday
    ).toEqual([1, 3]);
    expect(
      repeatDraftToPayload({ ...draft, freq: 'monthly' }).repeatByweekday
    ).toBeNull();
  });

  it('floors the interval at 1 so the backend never sees a 0', () => {
    expect(
      repeatDraftToPayload({
        freq: 'yearly',
        interval: 0,
        byweekday: [],
        until: '',
      }).repeatInterval
    ).toBe(1);
  });

  it('sends a blank end date as null, not an empty string', () => {
    expect(
      repeatDraftToPayload({
        freq: 'daily',
        interval: 1,
        byweekday: [],
        until: '',
      }).repeatUntil
    ).toBeNull();
  });
});
