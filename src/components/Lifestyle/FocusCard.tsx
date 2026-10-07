import { useQuery } from '@tanstack/react-query';
import { api } from '@/hooks/api';
import { focusBars, focusTotals, sessionLabel } from '@/lib/pomodoro';
import { CARD } from './card';

const DAYS = 14;
const VIEW_WIDTH = 320;
const HEIGHT = 56;

const DAY_FORMAT: Intl.DateTimeFormatOptions = {
  weekday: 'short',
  month: 'short',
  day: 'numeric',
};

/** A day key, read at local noon so no timezone can tip it onto a neighbour. */
function dayLabel(iso: string): string {
  return new Date(`${iso}T12:00:00`).toLocaleDateString([], DAY_FORMAT);
}

/**
 * The Watch's pomodoro timer: focus minutes per day and the latest runs.
 *
 * Its own card rather than a heatmap type — the heatmap's five hues are full,
 * and a block of focused work is not a workout.
 */
export function FocusCard() {
  const { data, isLoading } = useQuery({
    queryKey: ['lifestyle', 'pomodoro', DAYS],
    queryFn: () => api.lifestyle.pomodoro.summary(DAYS),
  });

  if (isLoading || !data) return null;

  if (data.sessions.length === 0) {
    return (
      <section className={CARD} aria-label="Focus">
        <h2 className="text-sm font-semibold text-[var(--color-text)]">
          Focus
        </h2>
        <p className="mt-2 text-sm text-[var(--color-text-muted)]">
          No timer runs yet. Start Focus or Timeout on the Watch.
        </p>
      </section>
    );
  }

  const bars = focusBars(data.days, VIEW_WIDTH, HEIGHT);
  const totals = focusTotals(data.days);
  const today = data.days[data.days.length - 1];

  return (
    <section className={CARD} aria-label="Focus">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-[var(--color-text)]">
          Focus
        </h2>
        <span className="text-xs text-[var(--color-text-muted)]">
          Today: {today?.focusMinutes ?? 0} min · {today?.completedBlocks ?? 0}{' '}
          blocks
        </span>
      </div>

      <div className="mt-3">
        <div className="flex items-baseline justify-between text-xs text-[var(--color-text-muted)]">
          <span>Focus minutes, last {DAYS} days</span>
          <span>
            {totals.minutes} min · {totals.blocks} blocks
          </span>
        </div>
        <svg
          viewBox={`0 0 ${VIEW_WIDTH} ${HEIGHT}`}
          preserveAspectRatio="none"
          className="mt-1 w-full h-14"
          role="img"
          aria-label={`Focus minutes per day, ${totals.minutes} minutes over ${DAYS} days`}
        >
          {bars.map(b => (
            <rect
              key={b.date}
              x={b.x}
              y={b.y}
              width={b.width}
              height={b.height}
              rx={1}
              fill="var(--color-accent)"
            >
              <title>
                {dayLabel(b.date)}: {b.minutes} min
              </title>
            </rect>
          ))}
        </svg>
      </div>

      <ul className="mt-3 flex flex-col gap-1 text-sm">
        {data.sessions.slice(0, 5).map(s => (
          <li key={s.id} className="flex justify-between gap-2">
            <span className="text-[var(--color-text)] truncate">
              {sessionLabel(s)}
            </span>
            <span className="text-[var(--color-text-muted)] shrink-0">
              {new Date(s.startedAt).toLocaleString([], {
                ...DAY_FORMAT,
                hour: 'numeric',
                minute: '2-digit',
              })}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
