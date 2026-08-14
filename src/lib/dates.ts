// The app-wide day boundary: every "day" concept in Lunaschal runs
// 4am-to-4am local time, except newspapers (an edition's date comes from the
// source site, not the user's day — see src/lib/newspapers.ts). A task
// completed, a workout logged, or a todo due "today" at 1am still belongs to
// the day that started the previous morning. Mirrors backend/day_boundary.py.
export const DAY_ROLLOVER_HOUR = 4;

/** The YYYY-MM-DD key of the 4am-anchored local day containing `now`. */
export function localDayKey(now: Date = new Date()): string {
  const shifted = new Date(now.getTime() - DAY_ROLLOVER_HOUR * 60 * 60 * 1000);
  const y = shifted.getFullYear();
  const m = String(shifted.getMonth() + 1).padStart(2, '0');
  const d = String(shifted.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

/** The wall-clock time a logical day begins, as an 'HH:MM:SS' suffix. */
export const DAY_ROLLOVER_TIME = `${String(DAY_ROLLOVER_HOUR).padStart(2, '0')}:00:00`;

/** Local ms timestamp at which a day key's window opens. Built from the wall
 * clock rather than by adding four hours to midnight, so the DST day that is
 * 23 or 25 hours long still starts at 4am on its own clock. */
export function dayStartMs(dayKey: string): number {
  return new Date(`${dayKey}T${DAY_ROLLOVER_TIME}`).getTime();
}

// Plain ISO-date arithmetic, below. Day strings are parsed as UTC and only ever
// compared or advanced as UTC, so stepping across a DST boundary can't drift a
// day. Nothing here reads the local clock — that is localDayKey's job alone.

export function parseISODate(iso: string): Date {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d));
}

export function toISODate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

export function addDays(iso: string, days: number): string {
  const date = parseISODate(iso);
  date.setUTCDate(date.getUTCDate() + days);
  return toISODate(date);
}
