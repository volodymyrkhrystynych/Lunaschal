// Pure helpers for the Lifestyle tab's focus card, which shows the Watch's
// pomodoro runs. No DOM, so they run in the node test environment.

import type { PomodoroDay, PomodoroSession } from '@/hooks/api';

export interface FocusBar {
  date: string;
  x: number;
  width: number;
  y: number;
  height: number;
  minutes: number;
}

/** One bar per day of focus minutes, oldest left; an empty day keeps its slot.
 *  The ceiling is at least one 25-minute block, so a single short run isn't
 *  drawn as a full-height day. */
export function focusBars(
  days: PomodoroDay[],
  width: number,
  height: number,
  gap = 2
): FocusBar[] {
  const max = Math.max(25, ...days.map(d => d.focusMinutes));
  const slot = days.length ? width / days.length : width;
  return days.map((d, i) => {
    const h = (d.focusMinutes / max) * height;
    return {
      date: d.date,
      x: i * slot + gap / 2,
      width: Math.max(1, slot - gap),
      y: height - h,
      height: h,
      minutes: d.focusMinutes,
    };
  });
}

export function focusTotals(days: PomodoroDay[]): {
  minutes: number;
  blocks: number;
} {
  return {
    minutes: days.reduce((a, d) => a + d.focusMinutes, 0),
    blocks: days.reduce((a, d) => a + d.completedBlocks, 0),
  };
}

const KIND_LABEL: Record<PomodoroSession['kind'], string> = {
  work: 'Focus',
  break: 'Break',
  timeout: 'Timeout',
};

/** "Focus · 25 min", "Focus · 12 of 25 min (cancelled)". */
export function sessionLabel(s: PomodoroSession): string {
  const spent = Math.round(
    (Date.parse(s.endedAt) - Date.parse(s.startedAt)) / 60000
  );
  const planned = Math.round(s.plannedSeconds / 60);
  const length = s.completed
    ? `${planned} min`
    : `${spent} of ${planned} min (cancelled)`;
  return `${KIND_LABEL[s.kind]} · ${length}`;
}
