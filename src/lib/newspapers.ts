// Pure date-math helpers for the Newspapers front-page archive view.
// Dates are plain 'YYYY-MM-DD' strings; parsed as UTC midnight so day-nav
// math isn't skewed by the local timezone offset.

export function shiftDateISO(date: string, days: number): string {
  const d = new Date(`${date}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().split('T')[0];
}

export function isFutureDate(date: string, now: Date = new Date()): boolean {
  const today = now.toISOString().split('T')[0];
  return date > today;
}
