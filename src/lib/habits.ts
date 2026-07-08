// Pure date/grid helpers for the habit tracker. No DOM, no fetch — vitest-able in node.

export interface HabitSchedule {
  scheduleType: 'daily' | 'weekdays' | 'per_week';
  scheduleDays: number[] | null; // 0=Mon..6=Sun (Python date.weekday() convention)
}

export type CellLevel = 0 | 1 | 2 | 3 | 4 | 'skipped';

/** Parse 'YYYY-MM-DD' at local midnight (avoids UTC shifting the day). */
export function parseISODate(iso: string): Date {
  return new Date(iso + 'T00:00:00');
}

export function toISODate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

/** JS getDay() is 0=Sun..6=Sat; remap to 0=Mon..6=Sun to match the backend. */
export function mondayWeekday(d: Date): number {
  return (d.getDay() + 6) % 7;
}

/**
 * GitHub-style grid: `numWeeks` columns of 7 ISO dates each, weeks starting
 * Monday, the last column being the week that contains `todayISO` (so it may
 * include future dates — render those disabled).
 */
export function gridWeeks(todayISO: string, numWeeks: number): string[][] {
  const today = parseISODate(todayISO);
  const monday = new Date(today);
  monday.setDate(today.getDate() - mondayWeekday(today));

  const weeks: string[][] = [];
  for (let w = numWeeks - 1; w >= 0; w--) {
    const col: string[] = [];
    for (let d = 0; d < 7; d++) {
      const cell = new Date(monday);
      cell.setDate(monday.getDate() - w * 7 + d);
      col.push(toISODate(cell));
    }
    weeks.push(col);
  }
  return weeks;
}

export function isScheduled(dateISO: string, schedule: HabitSchedule): boolean {
  if (schedule.scheduleType !== 'weekdays') return true;
  return (schedule.scheduleDays ?? []).includes(mondayWeekday(parseISODate(dateISO)));
}

/**
 * Heatmap intensity for a cell. Boolean done → 4; quantity buckets by
 * progress toward the target; skipped days get their own style.
 */
export function cellLevel(
  check: { status: 'done' | 'skipped'; value: number | null } | undefined,
  habit: { type: 'boolean' | 'quantity'; targetValue: number | null },
): CellLevel {
  if (!check) return 0;
  if (check.status === 'skipped') return 'skipped';
  if (habit.type === 'boolean') return 4;
  const target = habit.targetValue ?? 0;
  const value = check.value ?? 0;
  if (target <= 0 || value >= target) return 4;
  if (value <= 0) return 0;
  const ratio = value / target;
  if (ratio < 0.34) return 1;
  if (ratio < 0.67) return 2;
  return 3;
}

/** Click cycle for boolean habit cells: none → done → skipped → none. */
export function nextBooleanState(current: 'done' | 'skipped' | 'none'): 'done' | 'skipped' | 'none' {
  if (current === 'none') return 'done';
  if (current === 'done') return 'skipped';
  return 'none';
}
