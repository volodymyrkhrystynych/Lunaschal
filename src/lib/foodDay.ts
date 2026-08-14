// The food log groups entries into 4am-anchored days, the app-wide "logical
// day" convention (src/lib/dates.ts).
import { localDayKey } from './dates';

/** The YYYY-MM-DD key of the 4am-anchored local day containing this ISO timestamp. */
export function foodDayKey(iso: string): string {
  return localDayKey(new Date(iso));
}

export function foodDayLabel(dayKey: string): string {
  return new Intl.DateTimeFormat('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  }).format(new Date(`${dayKey}T00:00:00`));
}

export interface FoodDayGroup<T> {
  dayKey: string;
  label: string;
  items: T[];
}

/**
 * Buckets already time-sorted entries into 4am-day groups, merging only
 * consecutive entries that share a day key so the feed's existing order is
 * preserved.
 */
export function groupByFoodDay<T extends { createdAt: string }>(
  entries: T[]
): FoodDayGroup<T>[] {
  const groups: FoodDayGroup<T>[] = [];
  for (const entry of entries) {
    const dayKey = foodDayKey(entry.createdAt);
    const last = groups[groups.length - 1];
    if (last && last.dayKey === dayKey) {
      last.items.push(entry);
    } else {
      groups.push({ dayKey, label: foodDayLabel(dayKey), items: [entry] });
    }
  }
  return groups;
}
