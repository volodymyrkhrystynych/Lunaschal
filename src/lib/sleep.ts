// Where the asleep bands sit on the day view's timeline. Pure geometry, no
// DOM — the same reason src/lib/calendarDayLayout.ts exists.
//
// The backend keeps wake/sleep against a 4am-anchored day key
// (backend/day_boundary.py) and the day view now draws that same 4am-to-4am
// window, so the two finally share one frame: a bedtime of 01:30 is stored on
// the day that started the previous morning *and* is drawn near the bottom of
// that day's timeline. Positions below are offset minutes from the window's
// 4am start, matching src/lib/calendarDayLayout.ts.
//
// What still needs care is that the far end of either night lies outside the
// window by construction — the previous bedtime is in yesterday's window and
// the next wake is in tomorrow's — so both clamp to an edge rather than being
// drawn where they fall.

import { dayStartMs } from './dates';

export const MINUTES_PER_DAY = 24 * 60;

export type SleepSource = 'auto' | 'manual' | null;

export interface SleepDay {
  date: string;
  /** Unix seconds, not the ISO strings most of the API returns — these are
   * instants placed against a date's own midnight, and one of them routinely
   * belongs to a different calendar date. */
  wakeAt: number | null;
  sleepAt: number | null;
  wakeSource: SleepSource;
  sleepSource: SleepSource;
  /** The far ends of the two nights that touch this date, which live on the
   * neighbouring day keys. */
  previousSleepAt: number | null;
  nextWakeAt: number | null;
}

export interface SleepBand {
  kind: 'morning' | 'evening';
  startMinutes: number;
  endMinutes: number;
  label: string;
}

/** The 4am start of a day key's window, in unix seconds. */
export function dayStartOf(date: string): number {
  return dayStartMs(date) / 1000;
}

/** An instant as offset minutes from a day key's 4am start. Deliberately
 * unclamped — negative means "before this day began", over MINUTES_PER_DAY
 * means "after it ended", and both are how a band gets clamped to an edge or
 * dropped rather than drawn wrong. */
export function minutesFromDayStart(ts: number, date: string): number {
  return (ts - dayStartOf(date)) / 60;
}

/** 'HH:MM' local wall clock, matching how an event's own time renders. */
export function formatClock(ts: number): string {
  const d = new Date(ts * 1000);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

function clamp(minutes: number): number {
  return Math.max(0, Math.min(MINUTES_PER_DAY, minutes));
}

/**
 * The asleep spans to shade on one day key's 4am-to-4am window, in that
 * window's offset minutes.
 *
 * A band is only drawn from an end we actually know. An unknown *far* end falls
 * back to the edge of the window, because "asleep until 07:20" is true whether
 * or not we know when it started — but an unknown *near* end (no wake time, no
 * sleep time) draws nothing at all, since the band would be claiming a boundary
 * nobody recorded.
 */
export function sleepBands(day: SleepDay, date: string): SleepBand[] {
  const bands: SleepBand[] = [];

  if (day.wakeAt !== null) {
    const end = minutesFromDayStart(day.wakeAt, date);
    const start =
      day.previousSleepAt !== null
        ? minutesFromDayStart(day.previousSleepAt, date)
        : 0;
    if (end > 0 && end > start) {
      bands.push({
        kind: 'morning',
        startMinutes: clamp(start),
        endMinutes: clamp(end),
        label: `asleep · woke ${formatClock(day.wakeAt)}`,
      });
    }
  }

  if (day.sleepAt !== null) {
    const start = minutesFromDayStart(day.sleepAt, date);
    const end =
      day.nextWakeAt !== null
        ? minutesFromDayStart(day.nextWakeAt, date)
        : MINUTES_PER_DAY;
    // A 01:30 bedtime is inside this window, near its bottom — it is the night
    // this day ended with, not the next day's morning.
    if (start < MINUTES_PER_DAY && end > start) {
      bands.push({
        kind: 'evening',
        startMinutes: clamp(start),
        endMinutes: clamp(end),
        label: `asleep from ${formatClock(day.sleepAt)}`,
      });
    }
  }

  return bands;
}

/** 'HH:MM' for a time field, or '' when that end isn't set — what the editor
 * seeds its inputs with. */
export function clockValue(ts: number | null): string {
  return ts === null ? '' : formatClock(ts);
}
