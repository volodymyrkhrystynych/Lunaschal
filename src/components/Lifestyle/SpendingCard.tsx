import { useQuery } from '@tanstack/react-query';
import { api } from '@/hooks/api';
import {
  formatCents,
  spendBars,
  topCategories,
  windowTotal,
} from '@/lib/spending';
import { CARD } from './card';

const DAYS = 30;
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
 * Voluntary spending per 4am day, and where it went.
 *
 * Fed by both ways a purchase arrives — a line typed on the phone's Daily page
 * and a receipt captured like a meal. A receipt not read yet has no amount, so
 * it is left out of every bar and counted beside the total instead of being
 * drawn as $0.
 */
export function SpendingCard() {
  const { data, isLoading } = useQuery({
    queryKey: ['spending', 'trends', DAYS],
    queryFn: () => api.spending.trends(DAYS),
    refetchInterval: q =>
      q.state.data?.some(d => d.pendingCount > 0) ? 10000 : false,
  });

  if (isLoading || !data) return null;

  const total = windowTotal(data);
  const pending = data.reduce((n, d) => n + d.pendingCount, 0);
  const today = data[data.length - 1];

  if (total === 0 && pending === 0) {
    return (
      <section className={CARD} aria-label="Spending">
        <h2 className="text-sm font-semibold text-[var(--color-text)]">
          Spending
        </h2>
        <p className="mt-2 text-sm text-[var(--color-text-muted)]">
          Nothing logged in the last {DAYS} days. Add a purchase or photograph a
          receipt from the phone's Capture tab.
        </p>
      </section>
    );
  }

  const bars = spendBars(data, VIEW_WIDTH, HEIGHT);
  const top = topCategories(data);

  return (
    <section className={CARD} aria-label="Spending">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-[var(--color-text)]">
          Spending
        </h2>
        <span className="text-xs text-[var(--color-text-muted)] tabular-nums">
          Today: {formatCents(today?.totalCents ?? 0)}
          {today?.pendingCount ? ` + ${today.pendingCount} being read` : ''}
        </span>
      </div>

      <div className="mt-3">
        <div className="flex items-baseline justify-between text-xs text-[var(--color-text-muted)]">
          <span>Per day, last {DAYS} days</span>
          <span className="tabular-nums">
            {formatCents(total)}
            {pending > 0 ? ` · ${pending} not read yet` : ''}
          </span>
        </div>
        <svg
          viewBox={`0 0 ${VIEW_WIDTH} ${HEIGHT}`}
          preserveAspectRatio="none"
          className="mt-1 w-full h-14"
          role="img"
          aria-label={`Spending per day, ${formatCents(total)} over ${DAYS} days`}
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
                {dayLabel(b.date)}: {formatCents(b.totalCents)}
                {b.pendingCount ? ` (+${b.pendingCount} being read)` : ''}
              </title>
            </rect>
          ))}
        </svg>
      </div>

      {top.length > 0 && (
        <ul
          className="mt-3 flex flex-col gap-1 text-sm"
          aria-label="Top categories"
        >
          {top.map(c => (
            <li key={c.label} className="flex justify-between gap-2">
              <span className="text-[var(--color-text)] truncate">
                {c.label}
              </span>
              <span className="text-[var(--color-text-muted)] shrink-0 tabular-nums">
                {formatCents(c.cents)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
