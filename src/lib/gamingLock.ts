import type { GamingStatus } from '../hooks/api';

/** Monday first, matching the server's Python weekdays (0 = Monday). */
export const WEEKDAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

/** Shortest override reason; the server enforces the same. */
export const MIN_OVERRIDE_REASON = 10;

function clock(unix: number): string {
  const d = new Date(unix * 1000);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

/** Where today stands, in one line. Mirrors `GamingStatus.summary` in the
 *  iOS core word for word. */
export function gamingSummary(s: GamingStatus): string {
  if (!s.settings.enabled) return 'Off';
  if (!s.applies) return 'Not a lock day — games are open';
  if (s.locked)
    return `Locked until you tell the chat you're done with work${s.workEnd ? ` (work ends ${s.workEnd})` : ''}`;
  let text =
    s.method === 'override'
      ? 'Unlocked without the review'
      : 'Unlocked after the review';
  if (s.unlockedAt != null) text += ` at ${clock(s.unlockedAt)}`;
  if (s.workEnd) text += ` · work ended ${s.workEnd}`;
  return text;
}

export function acceptableReason(reason: string): boolean {
  return reason.trim().length >= MIN_OVERRIDE_REASON;
}

/** `days` with `day` switched on or off, sorted. */
export function toggleDay(days: number[], day: number): number[] {
  return (
    days.includes(day) ? days.filter(d => d !== day) : [...days, day]
  ).sort((a, b) => a - b);
}
