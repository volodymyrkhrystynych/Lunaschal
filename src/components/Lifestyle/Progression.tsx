import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/hooks/api';
import { ACTIVITY_COLORS, todayISO, type SeriesPoint } from '@/lib/lifestyle';
import { Sparkline } from './Sparkline';

/** Weight vs volume is still an open question in the doc, so both are plotted
 *  behind a toggle rather than one being picked blind. */
type Metric = 'weight' | 'volume';

const shortDate = (iso: string) => iso.slice(5).replace('-', '/');

function BodyWeightChart() {
  const queryClient = useQueryClient();
  const [value, setValue] = useState('');
  const [error, setError] = useState<string | null>(null);

  const { data: logs = [] } = useQuery({
    queryKey: ['lifestyle', 'weight'],
    queryFn: () => api.lifestyle.weight.list(),
  });

  const log = useMutation({
    mutationFn: (weight: number) => api.lifestyle.weight.log({ weight }),
    onSuccess: () => {
      setValue('');
      setError(null);
      queryClient.invalidateQueries({ queryKey: ['lifestyle', 'weight'] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const series: SeriesPoint[] = logs.map(l => ({
    label: shortDate(l.date),
    value: l.weight,
  }));
  const today = logs.find(l => l.date === todayISO());

  const submit = () => {
    const n = Number(value.trim());
    if (!value.trim() || !Number.isFinite(n)) {
      setError('Enter a number');
      return;
    }
    log.mutate(n);
  };

  return (
    <div className="min-w-0">
      <h3 className="text-sm font-medium text-[var(--color-text)] mb-1">
        Body weight
      </h3>
      <Sparkline
        series={series}
        color={ACTIVITY_COLORS.building}
        title="Body weight"
        formatValue={v => v.toFixed(1)}
        empty="No weigh-ins yet"
      />
      <div className="mt-2 flex gap-2">
        <input
          type="number"
          inputMode="decimal"
          step="0.1"
          value={value}
          onChange={e => setValue(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter') submit();
          }}
          placeholder={today ? `Today: ${today.weight}` : "Log today's weight"}
          className="flex-1 min-w-0 px-3 py-2 rounded bg-[var(--color-bg)] border border-white/10 text-[var(--color-text)] text-sm focus:outline-none focus:border-[var(--color-primary)]"
        />
        <button
          type="button"
          onClick={submit}
          disabled={log.isPending}
          className="px-3 py-2 rounded bg-[var(--color-primary)] text-white text-sm disabled:opacity-40 min-h-[44px]"
        >
          Log
        </button>
      </div>
      {error && (
        <div className="mt-1 text-xs text-[var(--color-accent)]">{error}</div>
      )}
      {today && (
        <div className="mt-1 text-xs text-[var(--color-text-muted)]">
          Logging again today corrects it rather than adding a point.
        </div>
      )}
    </div>
  );
}

function ExerciseChart() {
  const [selected, setSelected] = useState<string | null>(null);
  const [metric, setMetric] = useState<Metric>('weight');

  const { data: exercises = [] } = useQuery({
    queryKey: ['lifestyle', 'exercises'],
    queryFn: api.lifestyle.exercises.list,
  });

  // Default to the most-trained exercise until the user picks one.
  const name = selected ?? exercises[0]?.name ?? null;

  const { data: progression } = useQuery({
    queryKey: ['lifestyle', 'progression', name],
    queryFn: () => api.lifestyle.exercises.progression(name as string),
    enabled: name !== null,
  });

  const series: SeriesPoint[] = (progression?.points ?? [])
    .map(p => ({
      label: shortDate(p.date),
      value: (metric === 'weight' ? p.maxWeight : p.totalVolume) ?? 0,
    }))
    .filter(p => p.value > 0);

  return (
    <div className="min-w-0">
      <div className="flex items-center justify-between gap-2 mb-1 flex-wrap">
        <h3 className="text-sm font-medium text-[var(--color-text)]">
          Per-exercise
        </h3>
        <div className="flex items-center gap-1 p-0.5 rounded bg-[var(--color-bg)] border border-white/10">
          {(['weight', 'volume'] as Metric[]).map(m => (
            <button
              key={m}
              type="button"
              onClick={() => setMetric(m)}
              className={`px-2 py-0.5 rounded text-xs transition-colors ${
                metric === m
                  ? 'bg-[var(--color-primary)] text-white'
                  : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
              }`}
            >
              {m === 'weight' ? 'Top set' : 'Volume'}
            </button>
          ))}
        </div>
      </div>

      <select
        value={name ?? ''}
        onChange={e => setSelected(e.target.value || null)}
        disabled={exercises.length === 0}
        className="w-full mb-2 px-3 py-2 rounded bg-[var(--color-bg)] border border-white/10 text-[var(--color-text)] text-sm focus:outline-none focus:border-[var(--color-primary)] disabled:opacity-40"
      >
        {exercises.length === 0 && <option value="">No exercises yet</option>}
        {exercises.map(ex => (
          <option key={ex.name} value={ex.name}>
            {ex.displayName} ({ex.sessionCount})
          </option>
        ))}
      </select>

      <Sparkline
        series={series}
        color={ACTIVITY_COLORS.goodlife_alone}
        title={progression?.displayName ?? 'Exercise'}
        formatValue={v => (metric === 'weight' ? `${v}` : `${Math.round(v)}`)}
        empty="Log a workout to start this chart"
      />
    </div>
  );
}

/** Body-weight and per-exercise progression, side by side. */
export function Progression() {
  return (
    <section className="p-4 rounded-lg bg-[var(--color-surface)] border border-white/10">
      <h2 className="text-lg font-semibold text-[var(--color-text)] mb-3">
        Progression
      </h2>
      <div className="grid gap-6 md:grid-cols-2">
        <BodyWeightChart />
        <ExerciseChart />
      </div>
    </section>
  );
}
