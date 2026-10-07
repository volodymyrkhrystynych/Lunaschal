// Pure helpers for the Lifestyle tab's Apple Health card. No DOM, so they run
// in the node test environment like the rest of src/lib.

import type { HealthDay, HealthWorkout } from '@/hooks/api';

/** Apple's default daily Exercise ring. Drawn as a reference line, not a
 *  verdict — the card never colours a day as failed. */
export const EXERCISE_GOAL_MINUTES = 30;

export interface Bar {
  date: string;
  x: number;
  width: number;
  y: number;
  height: number;
  minutes: number | null;
}

/** One bar per day, oldest left. A day with no reading gets no bar but keeps
 *  its slot — closing the gap would draw four weeks as three. The ceiling is
 *  never below the goal, so a quiet month isn't stretched to fill the box. */
export function exerciseBars(
  days: HealthDay[],
  width: number,
  height: number,
  gap = 2
): { bars: Bar[]; max: number; goalY: number } {
  const values = days.map(d => d.exerciseMinutes ?? 0);
  const max = Math.max(EXERCISE_GOAL_MINUTES, ...values);
  const slot = days.length ? width / days.length : width;
  const barWidth = Math.max(1, slot - gap);
  const bars = days.map((d, i) => {
    const minutes = d.exerciseMinutes;
    const h = minutes ? (minutes / max) * height : 0;
    return {
      date: d.date,
      x: i * slot + gap / 2,
      width: barWidth,
      y: height - h,
      height: h,
      minutes,
    };
  });
  return {
    bars,
    max,
    goalY: height - (EXERCISE_GOAL_MINUTES / max) * height,
  };
}

/** Total and daily average over the days that have a reading. */
export function exerciseSummary(days: HealthDay[]): {
  total: number;
  average: number | null;
} {
  const known = days
    .map(d => d.exerciseMinutes)
    .filter((m): m is number => m !== null);
  const total = known.reduce((a, b) => a + b, 0);
  return {
    total: Math.round(total),
    average: known.length ? Math.round(total / known.length) : null,
  };
}

/** HealthKit's camelCase activity name, as a label: "highIntensityIntervalTraining"
 *  → "High intensity interval training". */
export function activityLabel(name: string): string {
  const words = name
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .toLowerCase()
    .trim();
  return words ? words[0].toUpperCase() + words.slice(1) : 'Workout';
}

/** "42 min · 3.4 km · 190 kcal", leaving out what wasn't recorded. */
export function workoutDetail(w: HealthWorkout): string {
  const parts = [`${Math.round(w.durationSeconds / 60)} min`];
  if (w.distanceMeters)
    parts.push(`${(w.distanceMeters / 1000).toFixed(1)} km`);
  if (w.energyKcal) parts.push(`${Math.round(w.energyKcal)} kcal`);
  return parts.join(' · ');
}

/** "Synced 5 min ago" style, from unix seconds. */
export function syncedAgo(lastSyncedAt: number, nowMs = Date.now()): string {
  const minutes = Math.max(0, Math.round((nowMs / 1000 - lastSyncedAt) / 60));
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}
