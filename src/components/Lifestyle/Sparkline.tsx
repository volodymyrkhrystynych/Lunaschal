import { useRef, useState } from 'react';
import {
  nearestPoint,
  plotSeries,
  type PlottedPoint,
  type SeriesPoint,
} from '@/lib/lifestyle';

interface SparklineProps {
  series: SeriesPoint[];
  color: string;
  /** Names the single series — the chart has no legend because there's only one. */
  title: string;
  formatValue: (value: number) => string;
  height?: number;
  empty?: string;
}

const VIEW_WIDTH = 320;

/**
 * A single-series line chart, hand-rolled in SVG rather than pulling in a
 * charting library for two sparklines. Draws in a fixed viewBox and scales to
 * its container, so it reflows on the Pocket 2's narrow screen without a
 * resize observer.
 *
 * Ships the hover layer by default: a crosshair plus a tooltip on the nearest
 * point, since a static line with no way to read a value is only half a chart.
 */
export function Sparkline({
  series,
  color,
  title,
  formatValue,
  height = 96,
  empty = 'No data yet',
}: SparklineProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [hovered, setHovered] = useState<PlottedPoint | null>(null);

  const { points, path, min, max } = plotSeries(series, VIEW_WIDTH, height, 8);

  if (points.length === 0) {
    return (
      <div
        className="flex items-center justify-center text-sm text-[var(--color-text-muted)]"
        style={{ height }}
      >
        {empty}
      </div>
    );
  }

  // Pointer x is in CSS pixels; the viewBox is fixed, so scale it back.
  const toViewX = (clientX: number) => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect || rect.width === 0) return 0;
    return ((clientX - rect.left) / rect.width) * VIEW_WIDTH;
  };

  return (
    <div className="relative">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${VIEW_WIDTH} ${height}`}
        preserveAspectRatio="none"
        className="w-full touch-none"
        style={{ height }}
        role="img"
        aria-label={`${title}: ${points.length} points, ${formatValue(min)} to ${formatValue(max)}`}
        onPointerMove={e =>
          setHovered(nearestPoint(points, toViewX(e.clientX)))
        }
        onPointerLeave={() => setHovered(null)}
      >
        {hovered && (
          <line
            x1={hovered.x}
            y1={0}
            x2={hovered.x}
            y2={height}
            stroke="var(--color-text-muted)"
            strokeWidth={1}
            opacity={0.4}
          />
        )}
        <path
          d={path}
          fill="none"
          stroke={color}
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
          vectorEffect="non-scaling-stroke"
        />
        {/* Only the endpoints get a permanent marker; a dot on every point
            turns a 90-day line into noise. */}
        {[points[0], points[points.length - 1]].map((p, i) => (
          <circle key={i} cx={p.x} cy={p.y} r={3} fill={color} />
        ))}
        {hovered && (
          <circle
            cx={hovered.x}
            cy={hovered.y}
            r={4.5}
            fill={color}
            stroke="var(--color-surface)"
            strokeWidth={2}
          />
        )}
      </svg>

      <div className="flex items-baseline justify-between text-xs text-[var(--color-text-muted)]">
        <span>{points[0].label}</span>
        <span className="text-[var(--color-text)]">
          {hovered
            ? `${hovered.label} · ${formatValue(hovered.value)}`
            : `${formatValue(min)} – ${formatValue(max)}`}
        </span>
        <span>{points[points.length - 1].label}</span>
      </div>
    </div>
  );
}
