// "Jul 24, 3:41 PM" (year appended outside the current year) — a note can sit
// unreviewed for days or weeks, so unlike chat's clock-only formatMessageTime
// this needs the date too.
export function formatNoteCreatedAt(
  iso: string,
  now: Date = new Date()
): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  return d.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    ...(d.getFullYear() === now.getFullYear() ? {} : { year: 'numeric' }),
    hour: 'numeric',
    minute: '2-digit',
  });
}
