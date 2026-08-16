import { useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/hooks/api';
import type { WorkoutSession } from '@/hooks/api';
import {
  ACTIVITY_COLORS,
  ACTIVITY_LABELS,
  ACTIVITY_TYPES,
  addDays,
  buildHeatmapGrid,
  metricCeiling,
  metricValue,
  monthLabels,
  shadeLevel,
  intensityText,
  shadeOpacity,
  todayISO,
  type HeatmapDay,
  type ShadeMetric,
} from '@/lib/lifestyle';
import { IntensityStars } from './IntensityStars';

const WEEKS = 53;
const CELL = 12;
const GAP = 3;
const WEEKDAY_LABELS = ['', 'Mon', '', 'Wed', '', 'Fri', ''];

function sessionSummary(session: WorkoutSession): string {
  const parts = [ACTIVITY_LABELS[session.locationType]];
  if (session.durationMinutes) parts.push(`${session.durationMinutes} min`);
  return parts.join(' · ');
}

function DayDetail({
  day,
}: {
  day: HeatmapDay & { sessions: WorkoutSession[] };
}) {
  return (
    <div className="mt-3 p-3 rounded-lg bg-[var(--color-bg)] border border-white/10">
      <div className="text-sm font-medium text-[var(--color-text)] mb-2">
        {day.date}
      </div>
      <ul className="space-y-2">
        {day.sessions.map(session => (
          <li key={session.id} className="text-sm">
            <div className="flex items-center gap-2">
              <span
                className="inline-block w-2.5 h-2.5 rounded-sm shrink-0"
                style={{ background: ACTIVITY_COLORS[session.locationType] }}
                aria-hidden="true"
              />
              <span className="text-[var(--color-text)]">
                {sessionSummary(session)}
              </span>
              {session.intensityRating != null && (
                <IntensityStars value={session.intensityRating} />
              )}
            </div>
            {session.exercises.length > 0 && (
              <div className="ml-4.5 mt-1 text-xs text-[var(--color-text-muted)]">
                {session.exercises
                  .map(
                    ex =>
                      `${ex.displayName}${ex.sets.length ? ` (${ex.sets.length} sets)` : ''}`
                  )
                  .join(', ')}
              </div>
            )}
            {session.notes && (
              <div className="ml-4.5 mt-1 text-xs text-[var(--color-text-muted)] whitespace-pre-wrap">
                {session.notes}
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * The GitHub-commits-style grid, repurposed: **activity type drives the hue and
 * the logged number drives the shade** (docs/lifestyle-tab.md §1). When a day
 * holds more than one activity the box takes the highest-priority one and a
 * corner mark records that something else happened too.
 *
 * Which number drives the shade is still an open question in the doc, so both
 * are logged and the toggle here defaults to duration — the more objective of
 * the two — until there's enough data to settle it.
 */
export function ActivityHeatmap() {
  const [metric, setMetric] = useState<ShadeMetric>('duration');
  const [selected, setSelected] = useState<string | null>(null);
  const gridRef = useRef<HTMLDivElement>(null);

  const end = todayISO();
  const start = addDays(end, -(WEEKS * 7));

  const { data: days = [], isLoading } = useQuery({
    queryKey: ['lifestyle', 'heatmap', start, end],
    queryFn: () => api.lifestyle.heatmap({ start, end }),
  });

  // The grid is oldest-to-newest, and only mounts once loading finishes, so
  // the scroll-to-right has to key off that flip rather than run once on
  // mount — today's column is the point of the view, not 53 weeks back.
  useLayoutEffect(() => {
    const el = gridRef.current;
    if (el) el.scrollLeft = el.scrollWidth;
  }, [isLoading]);

  const weeks = useMemo(() => buildHeatmapGrid(days, end, WEEKS), [days, end]);
  const ceiling = useMemo(() => metricCeiling(days, metric), [days, metric]);
  const months = useMemo(() => monthLabels(weeks), [weeks]);
  const selectedDay = days.find(d => d.date === selected) ?? null;

  const toggle = (id: ShadeMetric, label: string) => (
    <button
      key={id}
      type="button"
      onClick={() => setMetric(id)}
      className={`px-2.5 py-1 rounded text-xs transition-colors ${
        metric === id
          ? 'bg-[var(--color-primary)] text-white'
          : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
      }`}
    >
      {label}
    </button>
  );

  // No card shell of its own: it shares one with the momentum chart, which is
  // the same question asked two ways (what did I do, and is it trending).
  return (
    <div className="min-w-0">
      <div className="flex items-center justify-between gap-2 mb-3 flex-wrap">
        <h2 className="text-lg font-semibold text-[var(--color-text)]">
          Activity
        </h2>
        <div className="flex items-center gap-1 p-0.5 rounded-md bg-[var(--color-bg)] border border-white/10">
          <span className="pl-2 pr-1 text-xs text-[var(--color-text-muted)]">
            Shade by
          </span>
          {toggle('duration', 'Duration')}
          {toggle('intensity', 'Intensity')}
        </div>
      </div>

      {isLoading ? (
        <div className="text-sm text-[var(--color-text-muted)]">Loading…</div>
      ) : (
        // The full year is wider than a Pocket 2 screen, so the grid scrolls
        // inside its own container rather than the page scrolling sideways.
        <div ref={gridRef} className="overflow-x-auto pb-1">
          <div
            className="inline-flex flex-col gap-1"
            style={{ minWidth: 'min-content' }}
          >
            <div className="flex" style={{ paddingLeft: 30, gap: GAP }}>
              {weeks.map((_, column) => {
                const month = months.find(m => m.column === column);
                return (
                  <div
                    key={column}
                    className="text-[10px] text-[var(--color-text-muted)]"
                    style={{ width: CELL }}
                  >
                    {month?.label ?? ''}
                  </div>
                );
              })}
            </div>

            <div className="flex" style={{ gap: GAP }}>
              <div
                className="flex flex-col justify-between text-[10px] text-[var(--color-text-muted)]"
                style={{ width: 30, gap: GAP }}
              >
                {WEEKDAY_LABELS.map((label, i) => (
                  <div
                    key={i}
                    style={{ height: CELL, lineHeight: `${CELL}px` }}
                  >
                    {label}
                  </div>
                ))}
              </div>

              {weeks.map((week, w) => (
                <div key={w} className="flex flex-col" style={{ gap: GAP }}>
                  {week.map(cell => {
                    if (!cell.inRange) {
                      return (
                        <div
                          key={cell.date}
                          style={{ width: CELL, height: CELL }}
                        />
                      );
                    }
                    const day = cell.day;
                    const level = day
                      ? shadeLevel(metricValue(day, metric), ceiling)
                      : 0;
                    const label = day
                      ? `${cell.date}: ${ACTIVITY_LABELS[day.activityType]}${
                          day.durationMinutes
                            ? `, ${day.durationMinutes} min`
                            : ''
                        }${
                          // Spelled out, never as star glyphs — this string is
                          // the box's only accessible name.
                          day.intensityRating
                            ? `, intensity ${intensityText(day.intensityRating)}`
                            : ''
                        }${day.secondary ? ', plus another activity' : ''}`
                      : `${cell.date}: nothing logged`;
                    return (
                      <button
                        key={cell.date}
                        type="button"
                        title={label}
                        aria-label={label}
                        disabled={!day}
                        onClick={() =>
                          setSelected(selected === cell.date ? null : cell.date)
                        }
                        className={`relative rounded-[3px] ${
                          day ? 'cursor-pointer' : 'cursor-default'
                        } ${
                          selected === cell.date
                            ? 'ring-2 ring-[var(--color-text)]'
                            : ''
                        }`}
                        style={{
                          width: CELL,
                          height: CELL,
                          background: day
                            ? ACTIVITY_COLORS[day.activityType]
                            : 'rgba(255,255,255,0.06)',
                          opacity: day ? shadeOpacity(level) : 1,
                        }}
                      >
                        {day?.secondary && (
                          <span
                            className="absolute bottom-0 right-0 w-[4px] h-[4px] rounded-[1px]"
                            style={{ background: 'var(--color-text)' }}
                            aria-hidden="true"
                          />
                        )}
                      </button>
                    );
                  })}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-[var(--color-text-muted)]">
        {ACTIVITY_TYPES.map(type => (
          <span key={type} className="flex items-center gap-1.5">
            <span
              className="inline-block w-3 h-3 rounded-[3px]"
              style={{ background: ACTIVITY_COLORS[type] }}
              aria-hidden="true"
            />
            {ACTIVITY_LABELS[type]}
          </span>
        ))}
        <span className="flex items-center gap-1.5">
          <span className="relative inline-block w-3 h-3 rounded-[3px] bg-white/20">
            <span
              className="absolute bottom-0 right-0 w-[4px] h-[4px] rounded-[1px]"
              style={{ background: 'var(--color-text)' }}
            />
          </span>
          Second activity that day
        </span>
        <span className="flex items-center gap-1.5">
          Less
          {[1, 2, 3, 4].map(level => (
            <span
              key={level}
              className="inline-block w-3 h-3 rounded-[3px]"
              style={{
                background: ACTIVITY_COLORS.goodlife_alone,
                opacity: shadeOpacity(level as 1 | 2 | 3 | 4),
              }}
            />
          ))}
          More
        </span>
      </div>

      {selectedDay && <DayDetail day={selectedDay} />}
    </div>
  );
}
