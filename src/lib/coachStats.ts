import type { CoachStat } from '@/hooks/api';

/** "75%", or an em dash when nothing has resolved yet. */
export function percent(value: number | null): string {
  return value === null ? '—' : `${Math.round(value * 100)}%`;
}

/** The framing the coach currently leans toward, with its evidence count —
 *  or null while it has nothing to go on. "none" (holding back) is a
 *  framing like any other here: it can win. */
export function leadingFraming(
  stat: CoachStat
): { arm: string; mean: number; n: number } | null {
  const entries = Object.entries(stat.arms).filter(([, b]) => b.n >= 1);
  if (!entries.length) return null;
  const [arm, best] = entries.reduce((a, b) => (b[1].mean > a[1].mean ? b : a));
  return { arm, mean: best.mean, n: best.n };
}

/** Hours as "22:00" for the quiet-hours pickers. */
export function hourLabel(hour: number): string {
  return `${String(hour).padStart(2, '0')}:00`;
}
