/**
 * Shared `Intl.DateTimeFormat` instances.
 *
 * These exist because constructing one is expensive — it resolves a locale and
 * builds a pattern — and the Journal feed was constructing a fresh formatter
 * *per row, per render*, in four separate places. On a desktop that is
 * invisible; on the iPhone's JavaScriptCore it was the dominant cost of a feed
 * re-render, and the feed re-rendered on every keystroke in the composer.
 *
 * Module-level singletons are the fix rather than `useMemo`: the formatter
 * depends on nothing, so there is no reason for it to be per-component, and a
 * hook would have to be threaded through helpers that aren't components.
 */

/** "Mon, Jan 5, 2026, 09:41" — the Journal feed's timestamp. */
export const dateTimeFormat = new Intl.DateTimeFormat('en-US', {
  weekday: 'short',
  year: 'numeric',
  month: 'short',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
});

/** "09:41" — for rows already grouped under a date heading. */
export const timeFormat = new Intl.DateTimeFormat('en-US', {
  hour: '2-digit',
  minute: '2-digit',
});

/**
 * `undefined` is a real case, not defensiveness: `JournalEntry.createdAt` is
 * optional, because an optimistically inserted row exists before the server has
 * stamped one. A blank label is the right answer there — "Invalid Date" in the
 * feed is worse than no date at all.
 */
type Dateish = string | number | Date | null | undefined;

function format(fmt: Intl.DateTimeFormat, date: Dateish): string {
  if (date == null) return '';
  const d = new Date(date);
  return Number.isNaN(d.getTime()) ? '' : fmt.format(d);
}

export function formatDateTime(date: Dateish): string {
  return format(dateTimeFormat, date);
}

export function formatTime(date: Dateish): string {
  return format(timeFormat, date);
}

/** "Mon, Jan 5, 2026" — a day heading, no time. */
export const dayFormat = new Intl.DateTimeFormat('en-US', {
  weekday: 'short',
  year: 'numeric',
  month: 'short',
  day: 'numeric',
});

/** "Mon, Jan 5, 9:41 AM" — day and time without the year, for the food rows. */
export const dayTimeFormat = new Intl.DateTimeFormat('en-US', {
  weekday: 'short',
  month: 'short',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
});

export function formatDay(date: Dateish): string {
  return format(dayFormat, date);
}

export function formatDayTime(date: Dateish): string {
  return format(dayTimeFormat, date);
}
