import { useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/hooks/api';
import {
  nearestIndex,
  parseISODate,
  plotMultiSeries,
  type SeriesPoint,
} from '@/lib/lifestyle';

const VIEW_WIDTH = 320;
const HEIGHT = 120;
const WEEKS = 26;

/**
 * The two series, in fixed order with fixed hues — never cycled, never
 * reassigned by which one is larger this week. Checked with the dataviz
 * validator against the card surface (#313244): worst pair ΔE 24.9 (protan),
 * 28.0 normal, both inside the lightness band and over 3:1 contrast. They're
 * two of the four activity hues, reused rather than a new pair invented.
 */
const SERIES = [
  { key: 'applications', label: 'Applications sent', color: '#c17501' },
  { key: 'journalEntries', label: 'Journal entries', color: '#2f8fd8' },
] as const;

/** "Jul 20" — the Monday a week started. */
function weekLabel(iso: string): string {
  return parseISODate(iso).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  });
}

/**
 * Weekly momentum: job applications sent against journal entries written.
 *
 * Both are counts per week, so they share one y-axis — a second scale would let
 * the chart draw any relationship it liked. The floor is pinned at 0 rather than
 * the quietest week, or a normal week reads as a collapse.
 *
 * Hand-rolled SVG like `Sparkline`, which stays single-series (body weight, one
 * exercise's progression); this one carries a legend and a crosshair that reads
 * both lines at the same week.
 */
export function TrendsChart() {
  const svgRef = useRef<SVGSVGElement>(null);
  const [hovered, setHovered] = useState<number | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['lifestyle', 'trends', WEEKS],
    queryFn: () => api.lifestyle.trends(WEEKS),
  });
  const weeks = data?.weeks ?? [];

  const seriesList: SeriesPoint[][] = SERIES.map(s =>
    weeks.map(w => ({ label: weekLabel(w.weekStart), value: w[s.key] }))
  );
  // A ceiling of 0 (nothing logged all half-year) would divide by zero in the
  // projection; `plotMultiSeries` centres a flat domain, which reads fine.
  const { plots, max } = plotMultiSeries(seriesList, VIEW_WIDTH, HEIGHT, 8, {
    min: 0,
  });
  const hasData = weeks.length > 0;
  const active = hovered ?? (hasData ? weeks.length - 1 : null);

  // Pointer x is in CSS pixels; the viewBox is fixed, so scale it back.
  const toViewX = (clientX: number) => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect || rect.width === 0) return 0;
    return ((clientX - rect.left) / rect.width) * VIEW_WIDTH;
  };

  return (
    <section className="p-4 rounded-lg bg-[var(--color-surface)] border border-white/10 flex flex-col min-w-0">
      <div className="flex items-baseline justify-between gap-2 mb-1">
        <h2 className="text-lg font-semibold text-[var(--color-text)]">
          Momentum
        </h2>
        <span className="text-xs text-[var(--color-text-muted)]">
          per week · last {WEEKS}
        </span>
      </div>

      {/* Two series, so the legend is always present — identity is never
          carried by colour alone. */}
      <ul className="flex flex-wrap gap-x-4 gap-y-1 mb-2">
        {SERIES.map((s, i) => (
          <li
            key={s.key}
            className="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)]"
          >
            <span
              className="inline-block w-2.5 h-2.5 rounded-sm"
              style={{ background: s.color }}
              aria-hidden="true"
            />
            {s.label}
            {active !== null && (
              <span className="text-[var(--color-text)]">
                {seriesList[i][active]?.value ?? 0}
              </span>
            )}
          </li>
        ))}
      </ul>

      {isLoading && (
        <div className="text-[var(--color-text-muted)] text-sm">Loading…</div>
      )}

      {!isLoading && !hasData && (
        <div
          className="flex items-center justify-center text-sm text-[var(--color-text-muted)]"
          style={{ height: HEIGHT }}
        >
          No data yet
        </div>
      )}

      {hasData && (
        <>
          <svg
            ref={svgRef}
            viewBox={`0 0 ${VIEW_WIDTH} ${HEIGHT}`}
            preserveAspectRatio="none"
            className="w-full touch-none"
            style={{ height: HEIGHT }}
            role="img"
            aria-label={SERIES.map(
              (s, i) =>
                `${s.label}: ${weeks.length} weeks, 0 to ${max}, latest ${
                  seriesList[i][weeks.length - 1]?.value ?? 0
                }`
            ).join('. ')}
            onPointerMove={e =>
              setHovered(nearestIndex(plots[0].points, toViewX(e.clientX)))
            }
            onPointerLeave={() => setHovered(null)}
          >
            {hovered !== null && plots[0].points[hovered] && (
              <line
                x1={plots[0].points[hovered].x}
                y1={0}
                x2={plots[0].points[hovered].x}
                y2={HEIGHT}
                stroke="var(--color-text-muted)"
                strokeWidth={1}
                opacity={0.4}
              />
            )}

            {SERIES.map((s, i) => (
              <path
                key={s.key}
                d={plots[i].path}
                fill="none"
                stroke={s.color}
                strokeWidth={2}
                strokeLinecap="round"
                strokeLinejoin="round"
                vectorEffect="non-scaling-stroke"
              />
            ))}

            {/* The hovered week gets one marker per line, ringed in the surface
                colour so overlapping points stay two marks, not one blob. */}
            {hovered !== null &&
              SERIES.map((s, i) => {
                const p = plots[i].points[hovered];
                return p ? (
                  <circle
                    key={s.key}
                    cx={p.x}
                    cy={p.y}
                    r={4.5}
                    fill={s.color}
                    stroke="var(--color-surface)"
                    strokeWidth={2}
                  />
                ) : null;
              })}
          </svg>

          <div className="flex items-baseline justify-between text-xs text-[var(--color-text-muted)]">
            <span>{seriesList[0][0]?.label}</span>
            <span className="text-[var(--color-text)]">
              {active !== null ? `week of ${seriesList[0][active]?.label}` : ''}
            </span>
            <span>{seriesList[0][weeks.length - 1]?.label}</span>
          </div>
        </>
      )}
    </section>
  );
}
