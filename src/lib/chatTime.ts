// The chat view holds a single conversation per chat day (4am -> 4am), so a
// bare clock time is unambiguous — messages never span more than one night.
// Returns null for missing/unparseable input so callers can skip the element.
export function formatMessageTime(
  iso: string | null | undefined
): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (isNaN(d.getTime())) return null;
  return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
}
