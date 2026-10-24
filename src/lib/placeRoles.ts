import type {
  LocationPatterns,
  PlaceRole,
  PlaceRolePeriod,
} from '../hooks/api';
import { formatStay } from './whereabouts';

/** Whether a period covers a 4am day key. Ranges are [startedOn, endedOn). */
export function activeOn(period: PlaceRolePeriod, day: string): boolean {
  return (
    period.startedOn <= day && (period.endedOn == null || day < period.endedOn)
  );
}

/** The role a place holds on `day`, if any — at most one, by the server's
 *  rule that a place holds one role at a time. */
export function roleOf(
  placeId: string,
  periods: PlaceRolePeriod[],
  day: string
): PlaceRolePeriod | undefined {
  return periods.find(p => p.placeId === placeId && activeOn(p, day));
}

/** Each role's periods, newest start first: what the History list shows. */
export function historyByRole(
  periods: PlaceRolePeriod[]
): Record<PlaceRole, PlaceRolePeriod[]> {
  const sorted = [...periods].sort((a, b) =>
    b.startedOn.localeCompare(a.startedOn)
  );
  return {
    home: sorted.filter(p => p.role === 'home'),
    work: sorted.filter(p => p.role === 'work'),
  };
}

export function roleLabel(role: PlaceRole): string {
  return role === 'home' ? 'Home' : 'Work';
}

export function periodRange(period: PlaceRolePeriod): string {
  return period.endedOn
    ? `${period.startedOn} → ${period.endedOn}`
    : `since ${period.startedOn}`;
}

/** One line over the weekly patterns: office days of the days the phone
 *  recorded, and the average commute each way. Null with nothing recorded. */
export function patternsSummary(
  p: LocationPatterns | undefined
): string | null {
  if (!p || p.observedDays === 0) return null;
  const parts = [
    `Last ${Math.round(p.days / 7)} weeks: at work ${p.officeDays} of ${p.observedDays} recorded days`,
  ];
  if (p.commute.toWork.avgSeconds)
    parts.push(`commute in ${formatStay(p.commute.toWork.avgSeconds)}`);
  if (p.commute.toHome.avgSeconds)
    parts.push(`home in ${formatStay(p.commute.toHome.avgSeconds)}`);
  return parts.join(' · ');
}
