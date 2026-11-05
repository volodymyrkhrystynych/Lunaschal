import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type CoachSettings } from '../../hooks/api';
import { hourLabel, leadingFraming, percent } from '@/lib/coachStats';

const HOURS = Array.from({ length: 24 }, (_, h) => h);
const SELECT =
  'bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-2 py-1 text-sm';

/** The coach (backend/coach/): the evening check-in, reminders that learn
 *  which framing and time get things done, and the phones they go to. Off
 *  until switched on; the server re-reads every setting on its next tick. */
export function CoachSection() {
  const queryClient = useQueryClient();
  const { data: settings } = useQuery({
    queryKey: ['coach', 'settings'],
    queryFn: api.coach.settings,
  });
  const { data: stats } = useQuery({
    queryKey: ['coach', 'stats'],
    queryFn: api.coach.stats,
  });
  const { data: push } = useQuery({
    queryKey: ['coach', 'push'],
    queryFn: api.coach.pushStatus,
  });
  const [testResult, setTestResult] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (changes: Partial<CoachSettings>) =>
      api.coach.updateSettings(changes),
    onSuccess: data => queryClient.setQueryData(['coach', 'settings'], data),
  });
  const test = useMutation({
    mutationFn: api.coach.pushTest,
    onSuccess: r =>
      setTestResult(
        r.pushed
          ? `Sent to ${r.pushed} device${r.pushed === 1 ? '' : 's'}.`
          : `Not delivered${r.errors.length ? `: ${r.errors.join(', ')}` : ' — no phone has registered.'}`
      ),
    onError: e => setTestResult(e.message),
  });

  if (!settings) return <p className="text-sm">Loading…</p>;

  const hourSelect = (
    label: string,
    value: number,
    key: 'checkinHour' | 'quietStart' | 'quietEnd'
  ) => (
    <label className="flex items-center gap-2 text-sm">
      {label}
      <select
        aria-label={label}
        value={value}
        onChange={e => save.mutate({ [key]: Number(e.target.value) })}
        className={SELECT}
      >
        {HOURS.map(h => (
          <option key={h} value={h}>
            {hourLabel(h)}
          </option>
        ))}
      </select>
    </label>
  );

  return (
    <div className="space-y-4 text-sm">
      <div className="space-y-2">
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={settings.checkinEnabled}
            onChange={e => save.mutate({ checkinEnabled: e.target.checked })}
          />
          Evening check-in
        </label>
        {settings.checkinEnabled &&
          hourSelect('Check in at', settings.checkinHour, 'checkinHour')}
        <p className="text-xs text-[var(--color-text-muted)]">
          One message asking how the day went, listing what's still open. Your
          answer — a busy day, being ill — is recorded so yesterday's review and
          your streaks take it into account.
        </p>
      </div>

      <div className="space-y-2">
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={settings.enabled}
            onChange={e => save.mutate({ enabled: e.target.checked })}
          />
          Reminders that learn
        </label>
        <div className="flex flex-wrap gap-4">
          {(['weight', 'calories', 'habits'] as const).map(b => (
            <label key={b} className="flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={settings.behaviors[b]}
                onChange={e =>
                  save.mutate({
                    behaviors: { ...settings.behaviors, [b]: e.target.checked },
                  })
                }
              />
              {b === 'weight'
                ? 'Weigh-in'
                : b === 'calories'
                  ? 'Calories'
                  : 'Habits'}
            </label>
          ))}
        </div>
        <div className="flex flex-wrap gap-4">
          {hourSelect('Quiet from', settings.quietStart, 'quietStart')}
          {hourSelect('until', settings.quietEnd, 'quietEnd')}
          <label className="flex items-center gap-2">
            At most
            <select
              aria-label="Reminders per day"
              value={settings.dailyCap}
              onChange={e => save.mutate({ dailyCap: Number(e.target.value) })}
              className={SELECT}
            >
              {[1, 2, 3, 4, 5, 6].map(n => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
            a day
          </label>
        </div>
        <p className="text-xs text-[var(--color-text-muted)]">
          Each reminder is tried in one of a few framings and times, and a
          quarter are deliberately held back so the coach can tell what a
          reminder changed from what you'd have done anyway. Nothing is sent for
          something already logged, and a habit you keep doing on your own stops
          being prompted.
        </p>
        {save.error && (
          <p role="alert" className="text-red-400">
            {save.error.message}
          </p>
        )}
      </div>

      {stats && stats.length > 0 && (
        <table className="w-full text-xs">
          <thead className="text-left text-[var(--color-text-muted)]">
            <tr>
              <th className="font-normal">What</th>
              <th className="font-normal">Done with a reminder</th>
              <th className="font-normal">Without</th>
              <th className="font-normal">Leaning toward</th>
            </tr>
          </thead>
          <tbody>
            {stats.map(s => {
              const lead = leadingFraming(s);
              return (
                <tr key={s.behavior}>
                  <td>{s.label}</td>
                  <td>
                    {percent(s.doneWhenSent)}{' '}
                    <span className="text-[var(--color-text-muted)]">
                      (n={s.sent})
                    </span>
                  </td>
                  <td>
                    {percent(s.doneWhenHeld)}{' '}
                    <span className="text-[var(--color-text-muted)]">
                      (n={s.held})
                    </span>
                  </td>
                  <td>
                    {lead
                      ? `${lead.arm === 'none' ? 'not reminding' : lead.arm} (n≈${Math.round(lead.n)})`
                      : 'too early'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      <div className="space-y-1">
        <p>
          Push notifications:{' '}
          {push?.configured
            ? `configured · ${push.devices.filter(d => d.enabled).length} phone(s)`
            : 'not configured on the server (APNS_KEY_PATH / APNS_KEY_ID)'}
        </p>
        {push?.devices.map(d => (
          <p key={d.id} className="text-xs text-[var(--color-text-muted)]">
            {d.token} · {d.environment} · {d.enabled ? 'on' : 'off'}
            {d.lastError ? ` · ${d.lastError}` : ''}
          </p>
        ))}
        <button
          type="button"
          disabled={!push?.configured || test.isPending}
          onClick={() => test.mutate()}
          className="px-3 py-1.5 rounded bg-[var(--color-primary)]/20 text-[var(--color-primary)] border border-[var(--color-primary)]/40 disabled:opacity-50"
        >
          Send test
        </button>
        {testResult && <p role="status">{testResult}</p>}
        <p className="text-xs text-[var(--color-text-muted)]">
          Everything the coach says also lands in today's Chat, so nothing is
          lost without a phone.
        </p>
      </div>
    </div>
  );
}
