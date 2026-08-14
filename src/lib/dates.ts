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
