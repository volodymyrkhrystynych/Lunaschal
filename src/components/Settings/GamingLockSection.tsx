import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type GamingStatus } from '../../hooks/api';
import {
  WEEKDAY_LABELS,
  acceptableReason,
  gamingSummary,
  toggleDay,
} from '@/lib/gamingLock';

/** The gaming lock (backend/gaming/): on the chosen weekdays the server keeps
 *  game launchers closed until the user tells the chat they're done with
 *  work. The override is here, behind a typed reason, and it shows up as a
 *  missed item in the day's review. */
export function GamingLockSection() {
  const queryClient = useQueryClient();
  const { data: status } = useQuery({
    queryKey: ['gaming'],
    queryFn: api.gaming.status,
  });
  const [overriding, setOverriding] = useState(false);
  const [reason, setReason] = useState('');
  const [pauseInference, setPauseInference] = useState(true);
  const store = (data: GamingStatus) =>
    queryClient.setQueryData(['gaming'], data);
  const save = useMutation({
    mutationFn: (changes: Partial<GamingStatus['settings']>) =>
      api.gaming.updateSettings(changes),
    onSuccess: store,
  });
  const override = useMutation({
    mutationFn: () => api.gaming.override(reason, pauseInference),
    onSuccess: data => {
      store(data);
      setOverriding(false);
      setReason('');
    },
  });

  if (!status) return <p className="text-sm">Loading…</p>;
  const { settings } = status;

  return (
    <div className="space-y-3 text-sm">
      <label className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={settings.enabled}
          onChange={e => save.mutate({ enabled: e.target.checked })}
        />
        Lock games on workdays
      </label>
      {settings.enabled && (
        <div
          className="flex flex-wrap gap-1.5"
          role="group"
          aria-label="Lock days"
        >
          {WEEKDAY_LABELS.map((label, day) => {
            const on = settings.days.includes(day);
            return (
              <button
                key={label}
                type="button"
                aria-pressed={on}
                onClick={() =>
                  save.mutate({ days: toggleDay(settings.days, day) })
                }
                className={`px-2 py-0.5 rounded border text-xs ${
                  on
                    ? 'border-[var(--color-accent)] text-[var(--color-accent)]'
                    : 'border-white/10 text-[var(--color-text-muted)]'
                }`}
              >
                {label}
              </button>
            );
          })}
        </div>
      )}
      <p className="text-xs text-[var(--color-text-muted)]">
        On these days the server closes Steam, Battle.net, Lutris and Heroic
        until you tell the chat you're done with work. It goes over the day with
        you, then offers to unlock.
      </p>
      <p data-testid="gaming-status">{gamingSummary(status)}</p>
      {status.locked &&
        (overriding ? (
          <div className="space-y-2">
            <textarea
              aria-label="Why unlock without the review"
              placeholder="Why now, without closing out work?"
              value={reason}
              onChange={e => setReason(e.target.value)}
              className="w-full bg-[var(--color-bg)] border border-white/10 rounded px-2 py-1"
              rows={2}
            />
            <label className="flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                checked={pauseInference}
                onChange={e => setPauseInference(e.target.checked)}
              />
              Also pause GPU inference
            </label>
            <p className="text-xs text-[var(--color-text-muted)]">
              This counts as a missed item in today's review, with your reason.
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={!acceptableReason(reason) || override.isPending}
                onClick={() => override.mutate()}
                className="px-2 py-1 rounded border border-red-500/50 text-red-400 disabled:opacity-50"
              >
                Unlock anyway
              </button>
              <button type="button" onClick={() => setOverriding(false)}>
                Cancel
              </button>
            </div>
            {override.error && (
              <p className="text-xs text-red-400">{override.error.message}</p>
            )}
          </div>
        ) : (
          <button
            type="button"
            onClick={() => setOverriding(true)}
            className="text-xs text-red-400 underline"
          >
            Unlock without the review…
          </button>
        ))}
    </div>
  );
}
