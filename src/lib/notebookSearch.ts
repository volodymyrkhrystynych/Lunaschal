export function matchesQuery(name: string, query: string): boolean {
  if (!query.trim()) return true;
  return name.toLowerCase().includes(query.trim().toLowerCase());
}
