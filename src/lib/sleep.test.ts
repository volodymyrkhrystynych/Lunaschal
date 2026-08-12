import { describe, it, expect } from 'vitest';
import {
  clockValue,
  formatClock,
  midnightOf,
  minutesFromMidnight,
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

describe('minutesFromMidnight', () => {
  it('measures against the date it is given, not the timestamp', () => {
    expect(minutesFromMidnight(at(DATE, '07:20'), DATE)).toBe(7 * 60 + 20);
    expect(midnightOf(DATE)).toBe(at(DATE, '00:00'));
  });

  it('runs negative before the date and past the day after it', () => {
    // Unclamped on purpose: this is what tells sleepBands a value belongs to a
    // neighbouring date rather than this one.
    expect(minutesFromMidnight(at('2026-07-07', '23:00'), DATE)).toBe(-60);
    expect(minutesFromMidnight(at('2026-07-09', '01:30'), DATE)).toBe(
      MINUTES_PER_DAY + 90
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

  it('shades midnight to the wake time when the night before is unknown', () => {
    const [band] = sleepBands(day({ wakeAt: at(DATE, '07:20') }), DATE);
    expect(band).toMatchObject({
      kind: 'morning',
      startMinutes: 0,
      endMinutes: 440,
      label: 'asleep · woke 07:20',
    });
  });

  it('starts the morning band at last night bedtime when it fell after midnight', () => {
    // Went to bed at 01:30, up at 07:20: the first 90 minutes of the date were
    // awake and must not be shaded.
    const [band] = sleepBands(
      day({
        wakeAt: at(DATE, '07:20'),
        previousSleepAt: at(DATE, '01:30'),
      }),
      DATE
    );
    expect(band.startMinutes).toBe(90);
    expect(band.endMinutes).toBe(440);
  });

  it('clamps a bedtime from the previous evening to midnight', () => {
    const [band] = sleepBands(
      day({
        wakeAt: at(DATE, '07:20'),
        previousSleepAt: at('2026-07-07', '23:10'),
      }),
      DATE
    );
    expect(band.startMinutes).toBe(0);
  });

  it('shades the evening from the bedtime to midnight', () => {
    const [band] = sleepBands(day({ sleepAt: at(DATE, '23:40') }), DATE);
    expect(band).toMatchObject({
      kind: 'evening',
      startMinutes: 23 * 60 + 40,
      endMinutes: MINUTES_PER_DAY,
      label: 'asleep from 23:40',
    });
  });

  it('drops the evening band entirely when the bedtime is past midnight', () => {
    // 01:30 belongs to the *next* calendar date, where it is that date's
    // morning band. Drawing it here would shade an evening the user spent awake.
    const bands = sleepBands(day({ sleepAt: at('2026-07-09', '01:30') }), DATE);
    expect(bands).toEqual([]);
  });

  it('ends the evening band at a wake time that lands on the same date', () => {
    // A nap-shaped day: asleep 14:00, up again 16:00.
    const [band] = sleepBands(
      day({ sleepAt: at(DATE, '14:00'), nextWakeAt: at(DATE, '16:00') }),
      DATE
    );
    expect(band.startMinutes).toBe(14 * 60);
    expect(band.endMinutes).toBe(16 * 60);
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
