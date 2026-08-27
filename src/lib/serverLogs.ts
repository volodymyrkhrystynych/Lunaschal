/**
 * Presentation logic for Settings → Logs.
 *
 * Kept out of the component so it can be tested in the node environment, same
 * reason as the rest of src/lib. The backend (backend/ops/journal_logs.py)
 * decides what a journal line *is*; this decides how it reads and which lines
 * to show once the user starts filtering.
 */

export interface ServerLogEntry {
  /** ISO 8601 with offset, or null if the record had no usable timestamp. */
  ts: string | null;
  /** journald priority, 0 (emerg) … 7 (debug). */
  priority: number;
  identifier: string;
  message: string;
}

export interface ServerLogUnit {
  id: string;
  label: string;
  available: boolean;
}

export interface ServerLogResponse {
  available: boolean;
  unit: string;
  entries: ServerLogEntry[];
  note: string | null;
}

export interface PriorityMeta {
  label: string;
  /** Tailwind text-colour class, matching src/lib/backup.ts's TONE_CLASSES. */
  tone: string;
}

/** One entry for every journald priority so a lookup never misses. */
export const PRIORITY_META: Record<number, PriorityMeta> = {
  0: { label: 'emerg', tone: 'text-red-400' },
  1: { label: 'alert', tone: 'text-red-400' },
  2: { label: 'crit', tone: 'text-red-400' },
  3: { label: 'error', tone: 'text-red-400' },
  4: { label: 'warn', tone: 'text-amber-400' },
  5: { label: 'notice', tone: 'text-[var(--color-text)]' },
  6: { label: 'info', tone: 'text-[var(--color-text-muted)]' },
  7: { label: 'debug', tone: 'text-[var(--color-text-muted)]' },
};

export function priorityMeta(priority: number): PriorityMeta {
  return PRIORITY_META[priority] ?? PRIORITY_META[6];
}

/** Dropdown choices for `?since=`; keys match SINCE_PRESETS in the backend. */
export const SINCE_OPTIONS: { value: string; label: string }[] = [
  { value: '15m', label: 'Last 15 min' },
  { value: '1h', label: 'Last hour' },
  { value: '6h', label: 'Last 6 hours' },
  { value: '24h', label: 'Last 24 hours' },
  { value: '3d', label: 'Last 3 days' },
  { value: 'all', label: 'Everything kept' },
];

/** Dropdown choices for `?lines=`; values are clamped again server-side. */
export const LINE_OPTIONS: number[] = [200, 500, 1000, 2000];

/** Dropdown choices for the minimum-severity filter (client-side). */
export const PRIORITY_OPTIONS: { value: number; label: string }[] = [
  { value: 7, label: 'All' },
  { value: 6, label: 'Info and up' },
  { value: 5, label: 'Notice and up' },
  { value: 4, label: 'Warnings and up' },
  { value: 3, label: 'Errors only' },
];

const ACCESS_LOG_RE =
  /"(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) \S+ HTTP\/[\d.]+" \d{3}/;

export function isAccessLog(message: string): boolean {
  return ACCESS_LOG_RE.test(message);
}

function pad(n: number): string {
  return n < 10 ? `0${n}` : `${n}`;
}

/**
 * `HH:MM:SS` for an entry from today, `MMM D HH:MM:SS` otherwise. `now` is
 * injectable so the test does not depend on the wall clock.
 */
export function formatLogTimestamp(
  iso: string | null,
  now: Date = new Date()
): string {
  if (!iso) return '--:--:--';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '--:--:--';
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  if (sameDay) return time;
  const month = d.toLocaleString('en-US', { month: 'short' });
  return `${month} ${d.getDate()} ${time}`;
}

export interface LogFilter {
  query?: string;
  /** Keep entries with priority <= this (lower number = more severe). */
  maxPriority?: number;
  hideAccessLogs?: boolean;
}

export function filterEntries(
  entries: ServerLogEntry[],
  { query = '', maxPriority = 7, hideAccessLogs = false }: LogFilter
): ServerLogEntry[] {
  const q = query.trim().toLowerCase();
  return entries.filter(e => {
    if (e.priority > maxPriority) return false;
    if (hideAccessLogs && isAccessLog(e.message)) return false;
    if (q && !e.message.toLowerCase().includes(q)) return false;
    return true;
  });
}

/** Plain-text rendering of the visible log, for the Copy button. */
export function entriesToText(entries: ServerLogEntry[]): string {
  return entries
    .map(e => `${e.ts ?? ''} ${priorityMeta(e.priority).label}\t${e.message}`)
    .join('\n');
}
