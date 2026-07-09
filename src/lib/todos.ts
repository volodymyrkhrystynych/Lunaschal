// Split todos into the active list and the completed archive.
// Completed todos are ordered most-recently-finished first; ISO timestamps
// compare correctly as strings, and legacy rows without a completedAt sink
// to the bottom.
export function partitionTodos<T extends { done: boolean; completedAt: string | null }>(
  todos: T[],
): { active: T[]; completed: T[] } {
  const active = todos.filter((t) => !t.done);
  const completed = todos
    .filter((t) => t.done)
    .sort((a, b) => (b.completedAt ?? '').localeCompare(a.completedAt ?? ''));
  return { active, completed };
}

// "Jul 8, 9:46 PM" within the current year, "Jul 8, 2025, 9:46 PM" otherwise.
// `now` is injectable for tests.
export function formatCompletedAt(iso: string | null, now: Date = new Date()): string {
  if (!iso) return '';
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
