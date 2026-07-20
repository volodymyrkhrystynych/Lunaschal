// Split todos into the active list and the completed archive.
// Active todos with a due date come first, soonest due on top; the rest keep
// their creation order (sort is stable), so new due-less todos still append
// at the bottom. Completed todos are ordered most-recently-finished first;
// ISO timestamps compare correctly as strings, and legacy rows without a
// completedAt sink to the bottom.
export function partitionTodos<
  T extends { done: boolean; completedAt: string | null; due?: string | null },
>(todos: T[]): { active: T[]; completed: T[] } {
  const active = todos
    .filter(t => !t.done)
    .sort((a, b) => {
      if (a.due && b.due) return a.due.localeCompare(b.due);
      if (a.due || b.due) return a.due ? -1 : 1;
      return 0;
    });
  const completed = todos
    .filter(t => t.done)
    .sort((a, b) => (b.completedAt ?? '').localeCompare(a.completedAt ?? ''));
  return { active, completed };
}

// Bucket todos by their list; unknown/legacy values land in 'todo' so a bad
// value can never make an item invisible.
export function groupTodosByList<T extends { list: string }>(
  todos: T[]
): Record<'todo' | 'chores' | 'archive', T[]> {
  const buckets: Record<'todo' | 'chores' | 'archive', T[]> = {
    todo: [],
    chores: [],
    archive: [],
  };
  for (const t of todos) {
    if (t.list === 'chores' || t.list === 'archive') buckets[t.list].push(t);
    else buckets.todo.push(t);
  }
  return buckets;
}

// "Jul 24" (year appended outside the current year). Overdue compares local
// calendar dates, not raw timestamps, so "due today" is never overdue.
export function formatDue(
  iso: string | null,
  now: Date = new Date()
): { label: string; overdue: boolean } | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (isNaN(d.getTime())) return null;
  const label = d.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    ...(d.getFullYear() === now.getFullYear() ? {} : { year: 'numeric' }),
  });
  const dayKey = (x: Date) =>
    x.getFullYear() * 10000 + x.getMonth() * 100 + x.getDate();
  return { label, overdue: dayKey(d) < dayKey(now) };
}

// "every day", "every 2 weeks", "every 3 months".
export function repeatLabel(
  interval: number | null,
  unit: string | null
): string {
  if (!interval || !unit) return '';
  return interval === 1 ? `every ${unit}` : `every ${interval} ${unit}s`;
}

// 'YYYY-MM-DD' from a date input -> unix seconds at local noon. Noon keeps the
// calendar date stable through the backend's UTC ISO round-trip for any
// timezone within ±11 hours.
export function dueInputToUnix(dateStr: string): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateStr);
  if (!m) return null;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), 12, 0, 0);
  if (isNaN(d.getTime())) return null;
  return Math.floor(d.getTime() / 1000);
}

// ISO due string -> local 'YYYY-MM-DD' for prefilling an <input type="date">.
export function dueIsoToInput(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

// "Jul 8, 9:46 PM" within the current year, "Jul 8, 2025, 9:46 PM" otherwise.
// `now` is injectable for tests.
export function formatCompletedAt(
  iso: string | null,
  now: Date = new Date()
): string {
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
