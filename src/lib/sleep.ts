// Where the asleep bands sit on the day view's timeline. Pure geometry, no
// DOM — the same reason src/lib/calendarDayLayout.ts exists.
//
// The one thing worth understanding here: a night is not a day. The backend
// keeps wake/sleep against a 4am-anchored day key (backend/day_boundary.py), so a
// bedtime of 01:30 is stored on the day that started the previous morning and
// lands on the *next* calendar date. The day view, meanwhile, draws a plain
// calendar date from 00:00 to 24:00. Everything below is the intersection of
// those two: the night before this date ended at its wake time, the night after
// began at its sleep time, and either can fall outside the date entirely.

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

/** Local midnight of a 'YYYY-MM-DD' date, in unix seconds. */
export function midnightOf(date: string): number {
  return new Date(`${date}T00:00:00`).getTime() / 1000;
}

/** An instant as minutes from a date's midnight. Deliberately unclamped —
 * negative means "before this date began", over MINUTES_PER_DAY means "after it
 * ended", and both are how a band gets dropped rather than drawn wrong. */
export function minutesFromMidnight(ts: number, date: string): number {
  return (ts - midnightOf(date)) / 60;
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
 * The asleep spans to shade on one calendar date, in that date's minute space.
 *
 * A band is only drawn from an end we actually know. An unknown *far* end falls
 * back to the edge of the day (midnight), because "asleep until 07:20" is true
 * whether or not we know when it started — but an unknown *near* end (no wake
 * time, no sleep time) draws nothing at all, since the band would be claiming a
 * boundary nobody recorded.
 */
export function sleepBands(day: SleepDay, date: string): SleepBand[] {
  const bands: SleepBand[] = [];

  if (day.wakeAt !== null) {
    const end = minutesFromMidnight(day.wakeAt, date);
    const start =
      day.previousSleepAt !== null
        ? minutesFromMidnight(day.previousSleepAt, date)
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
    const start = minutesFromMidnight(day.sleepAt, date);
    const end =
      day.nextWakeAt !== null
        ? minutesFromMidnight(day.nextWakeAt, date)
        : MINUTES_PER_DAY;
    // A bedtime past midnight sits on the next date, where it is that date's
    // morning band instead. Nothing to draw here.
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
