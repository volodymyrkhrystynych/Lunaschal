import { useQuery } from '@tanstack/react-query';
import { api } from '../../hooks/api';

export function StatsPanel() {
  const { data } = useQuery({
    queryKey: ['practice', 'stats'],
    queryFn: api.practice.stats,
  });

  if (!data || data.totalAttempts === 0) return null;

  return (
    <div className="bg-[var(--color-surface)] rounded-lg border border-white/10 p-4 flex flex-col gap-3">
      <h2 className="text-sm font-semibold text-[var(--color-text)]">
        Overall progress
      </h2>
      <div className="text-sm text-[var(--color-text-muted)]">
        {data.totalAttempts} attempts · {data.avgWpm?.toFixed(0) ?? 0} avg wpm ·{' '}
        {data.avgAccuracy?.toFixed(0) ?? 0}% avg accuracy
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        {Object.entries(data.byLanguage).map(([lang, s]) => (
          <div
            key={lang}
            className="rounded-md bg-white/5 p-2 flex flex-col gap-0.5"
          >
            <div className="text-xs text-[var(--color-text-muted)] capitalize">
              {lang}
            </div>
            <div className="text-sm text-[var(--color-text)]">
              {s.attempts} · {s.avgWpm?.toFixed(0) ?? 0}wpm
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
