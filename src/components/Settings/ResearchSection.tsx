import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';

/**
 * The nightly repo-context job: what the app knows about its own codebase.
 * Hour defaults to 3 — the chat-title sweep owns 02:00-03:00 and the briefing
 * owns 05:00-07:00, so a 03:00-05:00 window contends with neither.
 */
export function ResearchSection() {
  const queryClient = useQueryClient();
  const [hourInput, setHourInput] = useState('3');

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.settings.get,
  });
  const { data: snapshot } = useQuery({
    queryKey: ['ideas', 'repo-context'],
    queryFn: api.ideas.repoContext,
  });

  useEffect(() => {
    if (settings) setHourInput(String(settings.repoContextHour ?? 3));
  }, [settings]);

  const invalidateSettings = () =>
    queryClient.invalidateQueries({ queryKey: ['settings'] });

  const toggleEnabled = useMutation({
    mutationFn: (enabled: boolean) =>
      api.settings.updateAI({ repoContextEnabled: enabled }),
    onSuccess: invalidateSettings,
  });

  const saveHour = useMutation({
    mutationFn: (hour: number) =>
      api.settings.updateAI({ repoContextHour: hour }),
    onSuccess: invalidateSettings,
  });

  const refresh = useMutation({
    mutationFn: api.ideas.refreshRepoContext,
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ['ideas', 'repo-context'] }),
  });

  return (
    <section className="space-y-3">
      <div>
        <h3 className="text-sm font-medium text-[var(--color-text)]">
          Repo context
        </h3>
        <p className="text-xs text-[var(--color-text-muted)] mt-1">
          A nightly scan of this codebase — routes, tables, views, settings — so
          the Ideas agent can tell what is already built. The scan itself is
          exact, not AI-generated; the model only summarizes what changed.
        </p>
      </div>

      <label className="flex items-center gap-2 text-sm text-[var(--color-text)]">
        <input
          type="checkbox"
          checked={!!settings?.repoContextEnabled}
          onChange={e => toggleEnabled.mutate(e.target.checked)}
        />
        Scan nightly
      </label>

      <label className="flex items-center gap-2 text-sm text-[var(--color-text)]">
        Hour
        <input
          type="number"
          min={0}
          max={23}
          value={hourInput}
          onChange={e => setHourInput(e.target.value)}
          onBlur={() => {
            const hour = Number(hourInput);
            if (Number.isInteger(hour) && hour >= 0 && hour <= 23)
              saveHour.mutate(hour);
            else setHourInput(String(settings?.repoContextHour ?? 3));
          }}
          className="w-16 rounded bg-[var(--color-bg)] border border-white/10 px-2 py-1 text-sm focus:outline-none focus:border-[var(--color-primary)]"
        />
        <span className="text-xs text-[var(--color-text-muted)]">
          runs in a two-hour window from this hour
        </span>
      </label>

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending}
          className="px-2 py-1 rounded text-sm bg-white/10 text-[var(--color-text)] hover:bg-white/15 disabled:opacity-50"
        >
          {refresh.isPending ? 'Scanning…' : 'Scan now'}
        </button>
        {snapshot ? (
          <span className="text-xs text-[var(--color-text-muted)]">
            {snapshot.routeCount} routes · {snapshot.tableCount} tables ·{' '}
            {snapshot.gitSha?.slice(0, 7) ?? 'no git'} ·{' '}
            {new Date(snapshot.generatedAt).toLocaleString()}
          </span>
        ) : (
          <span className="text-xs text-[var(--color-text-muted)]">
            Never scanned.
          </span>
        )}
      </div>

      {snapshot?.warnings?.map(warning => (
        <p key={warning} className="text-xs text-amber-400">
          {warning}
        </p>
      ))}

      {snapshot?.changeSummary && (
        <details className="text-xs text-[var(--color-text-muted)]">
          <summary className="cursor-pointer">What changed recently</summary>
          <p className="mt-1 whitespace-pre-wrap">{snapshot.changeSummary}</p>
        </details>
      )}
    </section>
  );
}
