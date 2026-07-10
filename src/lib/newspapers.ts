// Pure date-math helpers for the Newspapers front-page archive view.
// Dates are plain 'YYYY-MM-DD' strings. Once anchored, shifting by whole
// days is parsed as UTC midnight so the math isn't skewed by the local
// timezone offset — but the anchor itself ("today") must come from the
// viewer's local calendar date, matching the backend's `date.today()`
// (also local). Using `Date#toISOString()` for the anchor would read the
// *UTC* calendar date instead, which drifts a day off local for several
// hours around midnight depending on the timezone offset.

export function todayISO(now: Date = new Date()): string {
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

export function shiftDateISO(date: string, days: number): string {
  const d = new Date(`${date}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().split('T')[0];
}

export function isFutureDate(date: string, now: Date = new Date()): boolean {
  return date > todayISO(now);
}
