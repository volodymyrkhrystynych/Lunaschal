// AI-assigned calendar-event categories and their border colors. Mirrors the
// ACTIVITY_TYPES/ACTIVITY_COLORS pattern in src/lib/lifestyle.ts, kept as its
// own module because it's shared by the Calendar day view (event borders) and
// the Journal feed (event-group wrapper borders) — see journalEventGroups.ts.
//
// Colors are a fixed semantic mapping the user specified directly (outside=
// blue, work=green, family=purple, indoors=gray, leisure=beige, exercise=red),
// not a free choice of hues, so — unlike ACTIVITY_COLORS — there's no
// palette-validator pass backing these; they're a reasonable rendering of the
// requested names against the app's Catppuccin Mocha background/surface and
// worth revisiting only if a specific pair reads poorly in practice.

export const EVENT_CATEGORIES = [
  'leisure',
  'work',
  'exercise',
  'family',
  'outside',
  'indoors',
] as const;

export type EventCategory = (typeof EVENT_CATEGORIES)[number];

export const CATEGORY_LABELS: Record<EventCategory, string> = {
  leisure: 'Leisure',
  work: 'Work',
  exercise: 'Exercise',
  family: 'Family',
  outside: 'Outside',
  indoors: 'Indoors',
};

export const CATEGORY_COLORS: Record<EventCategory, string> = {
  outside: '#3b82f6',
  work: '#27a164',
  family: '#9b59b6',
  indoors: '#8a8f98',
  leisure: '#d9b382',
  exercise: '#e0475a',
};

export function isEventCategory(value: unknown): value is EventCategory {
  return (EVENT_CATEGORIES as readonly string[]).includes(value as string);
}

/** Parse a `categoryTags` JSON-array string (or null) into valid categories,
 * dropping anything outside the closed vocabulary. Never throws. */
export function parseCategoryTags(
  raw: string | null | undefined
): EventCategory[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isEventCategory);
  } catch {
    return [];
  }
}

/** CSS `box-shadow` value stacking one ring per category (innermost first),
 * so 1-3 categories render as concentric colored borders around an element
 * with no extra DOM nodes — used by both the Calendar day view's EventBlock
 * and the Journal feed's event-group wrapper, so the two visual languages
 * stay identical. Empty input -> ''. */
export function categoryRingBoxShadow(
  categories: EventCategory[],
  startPx = 2,
  stepPx = 3
): string {
  return categories
    .map((cat, i) => `0 0 0 ${startPx + i * stepPx}px ${CATEGORY_COLORS[cat]}`)
    .join(', ');
}
