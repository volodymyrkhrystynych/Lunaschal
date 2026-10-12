import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/hooks/api';
import { todayISO } from '@/lib/lifestyle';
import { CARD } from './card';

const duration = (minutes: number) => {
  const rounded = Math.round(minutes);
  return `${Math.floor(rounded / 60)}h ${rounded % 60}m`;
};
const dayLabel = (day: string) =>
  new Date(`${day}T12:00:00`).toLocaleDateString([], {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  });

export function SleepCard() {
  const today = todayISO();
  const [selected, setSelected] = useState<string>();
  const { data, isLoading, error } = useQuery({
    queryKey: ['lifestyle', 'sleep', today],
    queryFn: () => api.lifestyle.sleep(today),
    refetchInterval: 60000,
  });
  const days = data?.days ?? [];
  const night = days.find(d => d.date === selected) ?? days.at(-1);
  const maxMinutes = Math.max(
    480,
    ...days.map(d => (d.asleepMinutes ?? 0) + (d.awakeMinutes ?? 0))
  );

  return (
    <section className={CARD} aria-label="Sleep over the past week">
      <h2 className="text-sm font-semibold">Sleep · past 7 nights</h2>
      {isLoading && <p className="mt-2 text-sm">Loading sleep…</p>}
      {error && (
        <p role="alert" className="mt-2 text-sm">
          Could not refresh sleep. {error.message}
        </p>
      )}
      {days.length > 0 && (
        <>
          <p className="mt-2 text-xs text-[var(--color-text-muted)]">
            Hours asleep and recorded awake time. Select a night for details.
          </p>
          <div
            className="mt-3 flex gap-2"
            role="group"
            aria-label="Nightly sleep graph"
          >
            {days.map(d => (
              <button
                key={d.date}
                type="button"
                onClick={() => setSelected(d.date)}
                aria-pressed={night?.date === d.date}
                aria-label={`${dayLabel(d.date)}: ${d.asleepMinutes == null ? 'no sleep reading' : `${duration(d.asleepMinutes)} asleep, ${d.interruptions} recorded interruptions`}`}
                className="flex-1 min-w-0 rounded p-1 focus-visible:outline outline-2 outline-[var(--color-primary)] aria-pressed:bg-white/10"
              >
                <span
                  className="h-28 flex flex-col justify-end items-center border-b border-white/20"
                  aria-hidden="true"
                >
                  {d.asleepMinutes == null ? (
                    <span className="text-sm text-[var(--color-text-muted)]">
                      —
                    </span>
                  ) : (
                    <>
                      <span
                        className="w-full max-w-8 bg-[var(--color-accent)]"
                        style={{
                          height: `${((d.awakeMinutes ?? 0) / maxMinutes) * 100}%`,
                        }}
                      />
                      <span
                        className="w-full max-w-8 bg-[var(--color-primary)]"
                        style={{
                          height: `${(d.asleepMinutes / maxMinutes) * 100}%`,
                        }}
                      />
                    </>
                  )}
                </span>
                <span className="block mt-1 text-xs">{d.date.slice(5)}</span>
                <span className="block text-xs">
                  {d.asleepMinutes == null
                    ? '—'
                    : `${(d.asleepMinutes / 60).toFixed(1)}h`}
                </span>
              </button>
            ))}
          </div>
          <p className="mt-2 text-xs">
            <span className="inline-block w-2 h-2 bg-[var(--color-primary)]" />{' '}
            Asleep ·{' '}
            <span className="inline-block w-2 h-2 bg-[var(--color-accent)]" />{' '}
            Awake
          </p>
          {night && (
            <div className="mt-3 text-sm" aria-live="polite">
              <h3 className="font-medium">
                Night ending {dayLabel(night.date)}
              </h3>
              {night.asleepMinutes == null ? (
                <p className="mt-1 text-[var(--color-text-muted)]">
                  {night.start == null
                    ? 'No sleep reading. Enable sleep sharing in Apple Health and sync your iPhone.'
                    : 'Only time in bed was recorded; sleep duration and quality are unknown.'}
                </p>
              ) : (
                <>
                  <p className="mt-1">
                    {duration(night.asleepMinutes)} asleep ·{' '}
                    {night.interruptions} recorded interruptions ·{' '}
                    {Math.round(night.awakeMinutes ?? 0)} min awake
                  </p>
                  <p>
                    {night.efficiency == null
                      ? 'Sleep efficiency unavailable: incomplete recording.'
                      : `${Math.round(night.efficiency)}% sleep efficiency`}
                  </p>
                </>
              )}
            </div>
          )}
          <p className="mt-3 text-xs text-[var(--color-text-muted)]">
            Efficiency is time asleep divided by the sleep window, a measure of
            continuity. It does not measure how rested you feel. Gaps in
            recording are not counted as interruptions.
          </p>
        </>
      )}
    </section>
  );
}
