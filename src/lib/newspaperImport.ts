export function validIssueDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return (
    !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value
  );
}

/** Suggest only an unambiguous calendar date; never guess today's date. */
export function issueDateFromFilename(name: string): string {
  const dates = new Set<string>();
  for (const match of name.matchAll(
    /(?<!\d)(\d{4})[-_]?([01]\d)[-_]?([0-3]\d)(?!\d)/g
  )) {
    const date = `${match[1]}-${match[2]}-${match[3]}`;
    if (validIssueDate(date)) dates.add(date);
  }
  return dates.size === 1 ? [...dates][0] : '';
}
