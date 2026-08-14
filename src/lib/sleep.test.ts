import { describe, it, expect } from 'vitest';
import {
  clockValue,
  dayStartOf,
  formatClock,
  minutesFromDayStart,
  MINUTES_PER_DAY,
  sleepBands,
  type SleepDay,
} from './sleep';

const DATE = '2026-07-08';

/** Unix seconds for a local wall-clock time on a date — the tests' counterpart
 * to the backend's `at()` helper. */
const at = (date: string, hhmm: string) =>
  new Date(`${date}T${hhmm}:00`).getTime() / 1000;

const day = (overrides: Partial<SleepDay> = {}): SleepDay => ({
  date: DATE,
  wakeAt: null,
  sleepAt: null,
  wakeSource: null,
  sleepSource: null,
  previousSleepAt: null,
  nextWakeAt: null,
  ...overrides,
});

describe('minutesFromDayStart', () => {
  it('measures from 4am on the day key it is given, not from midnight', () => {
    expect(dayStartOf(DATE)).toBe(at(DATE, '04:00'));
    expect(minutesFromDayStart(at(DATE, '07:20'), DATE)).toBe(3 * 60 + 20);
  });

  it('places a past-midnight instant near the end of the window, not the start', () => {
    // The whole point of the 4am anchor: 01:30 is 21.5 hours into this day,
    // not 90 minutes into the next one.
    expect(minutesFromDayStart(at('2026-07-09', '01:30'), DATE)).toBe(
      21 * 60 + 30
    );
  });

  it('runs negative before the window and past its end after it', () => {
    // Unclamped on purpose: this is what tells sleepBands a value belongs to a
    // neighbouring day rather than this one.
    expect(minutesFromDayStart(at(DATE, '01:30'), DATE)).toBe(-150);
    expect(minutesFromDayStart(at('2026-07-09', '05:00'), DATE)).toBe(
      MINUTES_PER_DAY + 60
    );
  });
});

describe('formatClock', () => {
  it('is 24-hour and zero-padded, like an event time', () => {
    expect(formatClock(at(DATE, '07:05'))).toBe('07:05');
    expect(formatClock(at(DATE, '23:40'))).toBe('23:40');
    expect(clockValue(null)).toBe('');
  });
});

describe('sleepBands', () => {
  it('draws nothing when nothing is known', () => {
    expect(sleepBands(day(), DATE)).toEqual([]);
  });

  it('shades the top of the window down to the wake time', () => {
    const [band] = sleepBands(day({ wakeAt: at(DATE, '07:20') }), DATE);
    expect(band).toMatchObject({
      kind: 'morning',
      startMinutes: 0,
      endMinutes: 3 * 60 + 20,
      label: 'asleep · woke 07:20',
    });
  });

  it('clamps last night bedtime to the top however late it ran', () => {
    // Both of these are before this window opened — 01:30 belongs to the
    // previous day key now, not to this one's small hours — so each shades the
    // whole 04:00-to-07:20 stretch the user did sleep through.
    for (const previousSleepAt of [
      at(DATE, '01:30'),
      at('2026-07-07', '23:10'),
    ]) {
      const [band] = sleepBands(
        day({ wakeAt: at(DATE, '07:20'), previousSleepAt }),
        DATE
      );
      expect(band.startMinutes).toBe(0);
      expect(band.endMinutes).toBe(3 * 60 + 20);
    }
  });

  it('shades the evening from the bedtime to the end of the window', () => {
    const [band] = sleepBands(day({ sleepAt: at(DATE, '23:40') }), DATE);
    expect(band).toMatchObject({
      kind: 'evening',
      startMinutes: 19 * 60 + 40,
      endMinutes: MINUTES_PER_DAY,
      label: 'asleep from 23:40',
    });
  });

  it('keeps a past-midnight bedtime in this window, near its bottom', () => {
    // The night this day ended with. Under the old midnight-anchored view this
    // band was dropped here and redrawn as the next date's morning.
    const [band] = sleepBands(
      day({ sleepAt: at('2026-07-09', '01:30') }),
      DATE
    );
    expect(band).toMatchObject({
      kind: 'evening',
      startMinutes: 21 * 60 + 30,
      endMinutes: MINUTES_PER_DAY,
      label: 'asleep from 01:30',
    });
  });

  it('clamps a wake time in the next window to the bottom', () => {
    const [band] = sleepBands(
      day({
        sleepAt: at('2026-07-09', '01:30'),
        nextWakeAt: at('2026-07-09', '09:00'),
      }),
      DATE
    );
    expect(band.endMinutes).toBe(MINUTES_PER_DAY);
  });

  it('ends the evening band at a wake time inside the window', () => {
    // A nap-shaped day: asleep 14:00, up again 16:00.
    const [band] = sleepBands(
      day({ sleepAt: at(DATE, '14:00'), nextWakeAt: at(DATE, '16:00') }),
      DATE
    );
    expect(band.startMinutes).toBe(10 * 60);
    expect(band.endMinutes).toBe(12 * 60);
  });

  it('returns both bands for a fully-known day, morning first', () => {
    const bands = sleepBands(
      day({
        wakeAt: at(DATE, '07:20'),
        sleepAt: at(DATE, '23:40'),
        previousSleepAt: at('2026-07-07', '23:10'),
      }),
      DATE
    );
    expect(bands.map(b => b.kind)).toEqual(['morning', 'evening']);
  });

  it('draws no band from an end that would be inverted', () => {
    // A manual wake time earlier than the recorded bedtime before it: refuse
    // rather than render a negative-height band.
    const bands = sleepBands(
      day({ wakeAt: at(DATE, '07:20'), previousSleepAt: at(DATE, '09:00') }),
      DATE
    );
    expect(bands).toEqual([]);
  });
});
