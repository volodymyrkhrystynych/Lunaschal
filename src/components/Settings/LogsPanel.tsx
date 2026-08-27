import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import type { ServerLogResponse } from '../../hooks/api';
import {
  LINE_OPTIONS,
  PRIORITY_OPTIONS,
  SINCE_OPTIONS,
  entriesToText,
  filterEntries,
  formatLogTimestamp,
  priorityMeta,
} from '../../lib/serverLogs';

/**
 * Settings → Logs. A read-only window on the systemd --user journals
 * (backend/routes/logs.py) so the server's state — a stalled sync, a failed
 * deploy pull, llama crashing — is visible from the phone or the Pocket 2
 * without an SSH session.
 *
 * The backend returns up to `lines` recent entries for one unit; the severity,
 * search and "hide requests" filters are applied here over what came back.
 */
const selectClass =
  'bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 ' +
  'rounded px-2 py-1.5 text-sm focus:outline-none focus:border-[var(--color-primary)]';

export function LogsPanel() {
  const [unit, setUnit] = useState('lunaschal');
  const [lines, setLines] = useState(500);
  const [since, setSince] = useState('1h');
  const [maxPriority, setMaxPriority] = useState(7);
  const [query, setQuery] = useState('');
  const [hideAccessLogs, setHideAccessLogs] = useState(true);
  const [autoRefresh, setAutoRefresh] = useState(false);

  const units = useQuery({
    queryKey: ['logs', 'units'],
    queryFn: api.logs.units,
    staleTime: 60_000,
  });

  const logs = useQuery({
    queryKey: ['logs', unit, lines, since],
    queryFn: () => api.logs.get({ unit, lines, since }),
    refetchInterval: autoRefresh ? 5000 : false,
  });

  const entries = useMemo(
    () =>
      filterEntries(logs.data?.entries ?? [], {
        query,
        maxPriority,
        hideAccessLogs,
      }),
    [logs.data, query, maxPriority, hideAccessLogs]
  );

  // Auto-scroll to the newest line unless the user has scrolled up to read.
  const bodyRef = useRef<HTMLDivElement>(null);
  const pinnedToBottom = useRef(true);
  useEffect(() => {
    const el = bodyRef.current;
    if (el && pinnedToBottom.current) el.scrollTop = el.scrollHeight;
  }, [entries]);

  const onScroll = () => {
    const el = bodyRef.current;
    if (!el) return;
    pinnedToBottom.current =
      el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  };

  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(entriesToText(entries));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked — nothing useful to do */
    }
  };

  const data: ServerLogResponse | undefined = logs.data;

  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-[var(--color-text-muted)]">
        The systemd journals for the server's units. Read-only.
      </p>

      <div className="flex flex-wrap items-center gap-2">
        <select
          className={selectClass}
          value={unit}
          onChange={e => setUnit(e.target.value)}
        >
          {(
            units.data ?? [
              { id: 'lunaschal', label: 'App server', available: true },
            ]
          ).map(u => (
            <option key={u.id} value={u.id}>
              {u.label}
              {u.available ? '' : ' (not loaded)'}
            </option>
          ))}
        </select>

        <select
          className={selectClass}
          value={since}
          onChange={e => setSince(e.target.value)}
        >
          {SINCE_OPTIONS.map(o => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>

        <select
          className={selectClass}
          value={lines}
          onChange={e => setLines(Number(e.target.value))}
        >
          {LINE_OPTIONS.map(n => (
            <option key={n} value={n}>
              {n} lines
            </option>
          ))}
        </select>

        <select
          className={selectClass}
          value={maxPriority}
          onChange={e => setMaxPriority(Number(e.target.value))}
        >
          {PRIORITY_OPTIONS.map(o => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>

        <input
          type="text"
          placeholder="Filter…"
          value={query}
          onChange={e => setQuery(e.target.value)}
          className={selectClass + ' flex-1 min-w-[8rem]'}
        />
      </div>

      <div className="flex flex-wrap items-center gap-4 text-sm text-[var(--color-text)]">
        <label className="flex items-center gap-2 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={hideAccessLogs}
            onChange={e => setHideAccessLogs(e.target.checked)}
          />
          Hide requests
        </label>
        <label className="flex items-center gap-2 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={e => setAutoRefresh(e.target.checked)}
          />
          Auto-refresh
        </label>
        <button
          onClick={() => logs.refetch()}
          className="text-[var(--color-primary)] hover:underline disabled:opacity-50"
          disabled={logs.isFetching}
        >
          {logs.isFetching ? 'Refreshing…' : 'Refresh'}
        </button>
        <button
          onClick={copy}
          className="text-[var(--color-primary)] hover:underline"
          disabled={entries.length === 0}
        >
          {copied ? 'Copied' : 'Copy'}
        </button>
        <span className="text-[var(--color-text-muted)] ml-auto">
          {entries.length}
          {data && entries.length !== data.entries.length
            ? ` / ${data.entries.length}`
            : ''}{' '}
          lines
        </span>
      </div>

      {logs.isError ? (
        <p className="text-sm text-red-400">Could not read the logs.</p>
      ) : data && !data.available ? (
        <p className="text-sm text-[var(--color-text-muted)]">{data.note}</p>
      ) : (
        <div
          ref={bodyRef}
          onScroll={onScroll}
          className="h-[60vh] overflow-y-auto rounded border border-white/10 bg-[var(--color-bg)] p-2 font-mono text-xs leading-relaxed"
        >
          {logs.isLoading ? (
            <p className="text-[var(--color-text-muted)]">Loading…</p>
          ) : entries.length === 0 ? (
            <p className="text-[var(--color-text-muted)]">
              Nothing matches the current filters.
            </p>
          ) : (
            entries.map((e, i) => {
              const meta = priorityMeta(e.priority);
              return (
                <div
                  key={i}
                  className={`whitespace-pre-wrap break-all ${
                    e.priority <= 4 ? meta.tone : 'text-[var(--color-text)]'
                  }`}
                >
                  <span className="text-[var(--color-text-muted)]">
                    {formatLogTimestamp(e.ts)}{' '}
                  </span>
                  {e.priority <= 4 && (
                    <span className={meta.tone}>[{meta.label}] </span>
                  )}
                  {e.message}
                </div>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}
