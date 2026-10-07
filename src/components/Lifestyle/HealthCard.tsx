import { useQuery } from '@tanstack/react-query';
import { api } from '@/hooks/api';
import {
  activityLabel,
  exerciseBars,
  exerciseSummary,
  syncedAgo,
  workoutDetail,
} from '@/lib/appleHealth';
import { CARD } from './card';

const DAYS = 28;
const VIEW_WIDTH = 320;
const HEIGHT = 64;

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
 * What the Watch recorded, apart from what was logged by hand: Apple's
 * exercise minutes per day (every brisk walk, not only the sessions in the
 * workout log), steps, and the workouts the Watch tracked.
 *
 * Deliberately its own card rather than more boxes in the activity heatmap —
 * those five hues are full, and a gym session the user rated is a different
 * kind of fact from minutes a sensor counted.
 */
export function HealthCard() {
  const { data, isLoading } = useQuery({
    queryKey: ['lifestyle', 'health', DAYS],
    queryFn: () => api.lifestyle.health(DAYS),
  });

  if (isLoading || !data) return null;

  if (data.lastSyncedAt === null) {
    return (
      <section className={CARD} aria-label="Apple Health">
        <h2 className="text-sm font-semibold text-[var(--color-text)]">
          Apple Health
        </h2>
        <p className="mt-2 text-sm text-[var(--color-text-muted)]">
          Nothing from Apple Health yet. Turn on Health sync in the iPhone app's
          Settings.
        </p>
      </section>
    );
  }

  const { bars, goalY } = exerciseBars(data.days, VIEW_WIDTH, HEIGHT);
  const { total, average } = exerciseSummary(data.days);
  const today = data.days[data.days.length - 1];
  const workouts = data.workouts.slice(0, 5);

  return (
    <section className={CARD} aria-label="Apple Health">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-[var(--color-text)]">
          Apple Health
        </h2>
        <span className="text-xs text-[var(--color-text-muted)]">
          Synced {syncedAgo(data.lastSyncedAt)}
        </span>
      </div>

      <dl className="mt-3 grid grid-cols-3 gap-2 text-center">
        <div>
          <dt className="text-xs text-[var(--color-text-muted)]">
            Exercise today
          </dt>
          <dd className="text-lg text-[var(--color-text)]">
            {today?.exerciseMinutes != null
              ? `${Math.round(today.exerciseMinutes)} min`
              : '—'}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-[var(--color-text-muted)]">Steps</dt>
          <dd className="text-lg text-[var(--color-text)]">
            {today?.steps != null
              ? Math.round(today.steps).toLocaleString()
              : '—'}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-[var(--color-text-muted)]">
            Active energy
          </dt>
          <dd className="text-lg text-[var(--color-text)]">
            {today?.activeEnergyKcal != null
              ? `${Math.round(today.activeEnergyKcal)} kcal`
              : '—'}
          </dd>
        </div>
      </dl>

      <div className="mt-4">
        <div className="flex items-baseline justify-between text-xs text-[var(--color-text-muted)]">
          <span>Exercise minutes, last {DAYS} days</span>
          <span>
            {total} min{average !== null ? ` · ${average}/day` : ''}
          </span>
        </div>
        <svg
          viewBox={`0 0 ${VIEW_WIDTH} ${HEIGHT}`}
          preserveAspectRatio="none"
          className="mt-1 w-full h-16"
          role="img"
          aria-label={`Exercise minutes per day, ${total} minutes over ${DAYS} days`}
        >
          <line
            x1={0}
            x2={VIEW_WIDTH}
            y1={goalY}
            y2={goalY}
            stroke="currentColor"
            strokeOpacity={0.3}
            strokeDasharray="3 3"
            vectorEffect="non-scaling-stroke"
            className="text-[var(--color-text-muted)]"
          />
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
                {dayLabel(b.date)}:{' '}
                {b.minutes != null
                  ? `${Math.round(b.minutes)} min`
                  : 'no reading'}
              </title>
            </rect>
          ))}
        </svg>
        <div className="text-[10px] text-[var(--color-text-muted)]">
          dashed line: 30-minute goal
        </div>
      </div>

      {workouts.length > 0 && (
        <ul className="mt-4 flex flex-col gap-1 text-sm">
          {workouts.map(w => (
            <li key={w.id} className="flex justify-between gap-2">
              <span className="text-[var(--color-text)] truncate">
                {activityLabel(w.activityName)}
                <span className="text-[var(--color-text-muted)]">
                  {' '}
                  ·{' '}
                  {new Date(w.start * 1000).toLocaleDateString([], DAY_FORMAT)}
                </span>
              </span>
              <span className="text-[var(--color-text-muted)] shrink-0">
                {workoutDetail(w)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
