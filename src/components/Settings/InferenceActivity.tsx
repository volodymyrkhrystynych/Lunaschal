import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import {
  ACTIVITY_TONE,
  JOB_TONE,
  activityToText,
  countersSentence,
  eventLine,
  jobLine,
  orphanedKinds,
} from '../../lib/inferenceActivity';

/**
 * Settings → Logs: what the model service (backend/ai/service.py) has been
 * doing.
 *
 * It sits here rather than in the pause panel above the tabs because it is a
 * debugging tool, not a control — and this tab is where you already come when
 * the question is "what did the server do". The systemd journal below it has
 * the same lines interleaved with everything else the app prints; this is the
 * filtered view, and the only one a dev run can show at all, since there is no
 * `systemd --user` journal outside production.
 *
 * Collapsed by default, and the query is gated on that: the events are cheap
 * but the panel is long, and polling a log nobody has opened is the kind of
 * thing that quietly costs a request every five seconds all day.
 */
export function InferenceActivity() {
  const [open, setOpen] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [copied, setCopied] = useState(false);

  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['settings', 'inference', 'activity'],
    queryFn: () => api.settings.inferenceActivity(150),
    enabled: open,
    refetchInterval: open && autoRefresh ? 3000 : false,
  });

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(activityToText(data));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked — nothing useful to do */
    }
  };

  const orphans = data ? orphanedKinds(data.jobs, data.handlers) : [];
  const counts = data?.jobCounts ?? {};

  return (
    <section className="rounded border border-white/10 bg-[var(--color-surface)]">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex items-center gap-2 w-full text-left p-3"
      >
        <span className="w-4 shrink-0 text-[var(--color-text-muted)]">
          {open ? '▾' : '▸'}
        </span>
        <span className="font-medium text-[var(--color-text)]">
          LLM service activity
        </span>
        <span className="text-sm text-[var(--color-text-muted)] ml-auto">
          {counts.pending ? `${counts.pending} queued` : ''}
          {counts.error ? ` · ${counts.error} failed` : ''}
        </span>
      </button>

      {open && (
        <div className="px-3 pb-3 flex flex-col gap-3">
          <p className="text-sm text-[var(--color-text-muted)]">
            {countersSentence(data?.counters)}
          </p>

          <div className="flex flex-wrap items-center gap-4 text-sm">
            <label className="flex items-center gap-2 cursor-pointer select-none text-[var(--color-text)]">
              <input
                type="checkbox"
                checked={autoRefresh}
                onChange={e => setAutoRefresh(e.target.checked)}
              />
              Auto-refresh
            </label>
            <button
              onClick={() => refetch()}
              disabled={isFetching}
              className="text-[var(--color-primary)] hover:underline disabled:opacity-50"
            >
              {isFetching ? 'Refreshing…' : 'Refresh'}
            </button>
            <button
              onClick={copy}
              disabled={!data}
              className="text-[var(--color-primary)] hover:underline disabled:opacity-50"
            >
              {copied ? 'Copied' : 'Copy'}
            </button>
          </div>

          {orphans.length > 0 && (
            <p className="text-sm text-red-400">
              No handler is registered for {orphans.join(', ')} — jobs of that
              kind will sit queued forever.
            </p>
          )}

          {isLoading ? (
            <p className="text-sm text-[var(--color-text-muted)]">Loading…</p>
          ) : (
            <div className="grid gap-3 lg:grid-cols-2">
              <div>
                <h4 className="text-sm font-medium text-[var(--color-text)] mb-1">
                  Model calls
                </h4>
                <div className="h-64 overflow-y-auto rounded border border-white/10 bg-[var(--color-bg)] p-2 font-mono text-xs leading-relaxed">
                  {!data?.events.length ? (
                    <p className="text-[var(--color-text-muted)]">
                      Nothing since the server started.
                    </p>
                  ) : (
                    data.events.map((e, i) => {
                      const line = eventLine(e);
                      return (
                        <div
                          key={i}
                          className={`whitespace-pre-wrap break-all ${ACTIVITY_TONE[line.tone]}`}
                        >
                          <span className="text-[var(--color-text-muted)]">
                            {line.time}{' '}
                          </span>
                          {line.badge && (
                            <span className="text-[var(--color-text-muted)]">
                              [{line.badge}]{' '}
                            </span>
                          )}
                          {line.text}
                        </div>
                      );
                    })
                  )}
                </div>
              </div>

              <div>
                <h4 className="text-sm font-medium text-[var(--color-text)] mb-1">
                  Background queue
                </h4>
                <div className="h-64 overflow-y-auto rounded border border-white/10 bg-[var(--color-bg)] p-2 font-mono text-xs leading-relaxed">
                  {!data?.jobs.length ? (
                    <p className="text-[var(--color-text-muted)]">
                      No jobs recorded.
                    </p>
                  ) : (
                    data.jobs.map(job => (
                      <div
                        key={job.id}
                        className={`whitespace-pre-wrap break-all ${ACTIVITY_TONE[JOB_TONE[job.status]]}`}
                      >
                        <span className="text-[var(--color-text-muted)]">
                          {job.status}{' '}
                        </span>
                        {jobLine(job)}
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>
          )}

          <p className="text-xs text-[var(--color-text-muted)]">
            Model calls are held in memory and reset when the server restarts —
            the same lines are in the App server journal below. Queue rows are
            stored, so they survive a restart.
          </p>
        </div>
      )}
    </section>
  );
}
