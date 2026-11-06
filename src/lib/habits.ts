import { addDays } from './dates';

/** The last `count` 4am days ending at `today`, oldest first, with whether each
 *  was ticked. `history` is `GET /api/tasks`'s done days for one daily task. */
export function recentDays(
  history: readonly string[] | undefined,
  today: string,
  count = 7,
  excused: readonly string[] | undefined = undefined
): { day: string; done: boolean; excused: boolean }[] {
  const done = new Set(history ?? []);
  const off = new Set(excused ?? []);
  return Array.from({ length: count }, (_, i) => {
    const day = addDays(today, i - (count - 1));
    return {
      day,
      done: done.has(day),
      excused: !done.has(day) && off.has(day),
    };
  });
}

/** "🔥 5" while a streak is running; nothing for none. The server counts it,
 *  and keeps a streak alive through an unticked today. */
export function streakLabel(streak: number | undefined): string | null {
  return streak && streak > 0 ? `🔥 ${streak}` : null;
}
