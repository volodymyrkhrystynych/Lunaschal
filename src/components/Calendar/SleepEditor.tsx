import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { clockValue } from '@/lib/sleep';

/**
 * Corrects the day's wake and sleep times by hand.
 *
 * Both fields start seeded with whatever is currently shown, derived or not,
 * because the common edit is nudging a value that is nearly right. Clearing a
 * field hands that end back to the automatic derivation rather than blanking
 * it — which is why "Clear" and an empty field mean the same thing here, and
 * why neither is destructive.
 */
export function SleepEditor({
  date,
  onClose,
}: {
  date: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState({ wake: '', sleep: '' });
  const [error, setError] = useState<string | null>(null);

  const { data: sleep, isLoading } = useQuery({
    queryKey: ['calendar', 'sleep', date],
    queryFn: () => api.calendar.sleep.get(date),
  });

  // Seeded once the day arrives, and not on every render of it: a refetch
  // landing mid-edit must not overwrite what is being typed.
  useEffect(() => {
    if (!sleep) return;
    setDraft({
      wake: clockValue(sleep.wakeAt),
      sleep: clockValue(sleep.sleepAt),
    });
  }, [sleep?.date]); // eslint-disable-line react-hooks/exhaustive-deps

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['calendar', 'sleep'] });
    onClose();
  };

  const save = useMutation({
    mutationFn: () =>
      api.calendar.sleep.set(date, {
        ...(draft.wake ? { wake: draft.wake } : {}),
        ...(draft.sleep ? { sleep: draft.sleep } : {}),
      }),
    onSuccess: invalidate,
    onError: (e: Error) => setError(e.message),
  });

  const clear = useMutation({
    mutationFn: () => api.calendar.sleep.clear(date),
    onSuccess: invalidate,
    onError: (e: Error) => setError(e.message),
  });

  const sourceNote = (source: 'auto' | 'manual' | null) =>
    source === 'manual'
      ? 'set by you'
      : source === 'auto'
        ? 'from activity'
        : '';

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50"
      onClick={onClose}
    >
      <div
        className="bg-[var(--color-surface)] rounded-lg p-6 max-w-sm w-full mx-4"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-start justify-between mb-4">
          <div>
            <h2 className="text-xl font-semibold text-[var(--color-text)]">
              Sleep
            </h2>
            <div className="text-sm text-[var(--color-text-muted)] mt-1">
              {new Date(date + 'T00:00:00').toLocaleDateString('en-US', {
                weekday: 'long',
                month: 'long',
                day: 'numeric',
              })}
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
          >
            ✕
          </button>
        </div>

        {isLoading ? (
          <div className="text-sm text-[var(--color-text-muted)]">Loading…</div>
        ) : (
          <div className="space-y-4">
            <label className="block">
              <div className="flex items-baseline justify-between text-sm text-[var(--color-text)] mb-1">
                <span>Woke up</span>
                <span className="text-xs text-[var(--color-text-muted)]">
                  {sourceNote(sleep?.wakeSource ?? null)}
                </span>
              </div>
              <input
                type="time"
                aria-label="Woke up"
                value={draft.wake}
                onChange={e => setDraft({ ...draft, wake: e.target.value })}
                className="w-full bg-transparent text-[var(--color-text)] border-b border-white/10 pb-2 focus:outline-none"
              />
            </label>

            <label className="block">
              <div className="flex items-baseline justify-between text-sm text-[var(--color-text)] mb-1">
                <span>Went to sleep</span>
                <span className="text-xs text-[var(--color-text-muted)]">
                  {sourceNote(sleep?.sleepSource ?? null)}
                </span>
              </div>
              <input
                type="time"
                aria-label="Went to sleep"
                value={draft.sleep}
                onChange={e => setDraft({ ...draft, sleep: e.target.value })}
                className="w-full bg-transparent text-[var(--color-text)] border-b border-white/10 pb-2 focus:outline-none"
              />
            </label>

            {/* The day runs 4am to 4am, so a bedtime in the small hours belongs
                to this day and not the next one. Saying so is cheaper than
                having the user work it out from a time that moved. */}
            <p className="text-xs text-[var(--color-text-muted)]">
              A time before 4am counts as this day — a 01:30 bedtime is tonight,
              not tomorrow. Leave a field empty to go back to using when you
              were active.
            </p>

            {error && (
              <div className="text-xs text-[var(--color-error,#e0475a)]">
                {error}
              </div>
            )}

            <div className="flex items-center justify-between gap-2 pt-2">
              <button
                type="button"
                onClick={() => clear.mutate()}
                disabled={clear.isPending}
                className="px-3 py-2 min-h-[44px] text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
              >
                Clear
              </button>
              <button
                type="button"
                onClick={() => {
                  setError(null);
                  save.mutate();
                }}
                disabled={save.isPending}
                className="px-4 py-2 min-h-[44px] rounded bg-[var(--color-primary)] text-white text-sm disabled:opacity-50"
              >
                Save
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
