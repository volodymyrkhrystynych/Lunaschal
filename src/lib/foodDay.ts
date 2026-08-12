// The food log groups entries into 4am-anchored days, the same "logical day"
// convention used for chat/sleep/paper elsewhere in the app (backend's
// DAY_ROLLOVER_HOUR in backend/chat_day.py): a meal logged at 2am still
// belongs to the day that started the previous morning, not the calendar day
// that just ticked over at local midnight.
const DAY_ROLLOVER_HOUR = 4;

/** The YYYY-MM-DD key of the 4am-anchored local day containing this ISO timestamp. */
export function foodDayKey(iso: string): string {
  const shifted = new Date(
    new Date(iso).getTime() - DAY_ROLLOVER_HOUR * 60 * 60 * 1000
  );
  const y = shifted.getFullYear();
  const m = String(shifted.getMonth() + 1).padStart(2, '0');
  const d = String(shifted.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
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
