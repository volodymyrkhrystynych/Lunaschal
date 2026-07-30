// Pure helpers for the Lifestyle view — heatmap gridding, chart geometry, and
// calorie-line parsing — extracted so they can be unit-tested in the node
// environment (no jsdom), the same way src/lib/journalFeed.ts and
// src/lib/todos.ts are.

export const ACTIVITY_TYPES = [
  'goodlife_brother',
  'goodlife_alone',
  'building',
  'outside',
] as const;

export type ActivityType = (typeof ACTIVITY_TYPES)[number];

export const ACTIVITY_LABELS: Record<ActivityType, string> = {
  goodlife_brother: 'Goodlife with brother',
  goodlife_alone: 'Goodlife alone',
  building: 'Building workout room',
  outside: 'Outside',
};

// Four categorical hues, one per activity type, assigned in fixed order and
// never cycled. Checked with the dataviz validator against both the page
// background (#1e1e2e) and the card surface (#313244): all pairs clear the
// OKLCH lightness band, the chroma floor, CVD separation (worst pair ΔE 8.5
// deutan), and 3:1 contrast. Colour alone never carries identity here — the
// legend and the per-day tooltip both name the activity.
export const ACTIVITY_COLORS: Record<ActivityType, string> = {
  goodlife_brother: '#c17501',
  goodlife_alone: '#27a164',
  building: '#2f8fd8',
  outside: '#bc65a9',
};

export function isActivityType(value: unknown): value is ActivityType {
  return ACTIVITY_TYPES.includes(value as ActivityType);
}

// --- Dates -------------------------------------------------------------------
// ISO day strings are parsed as UTC and only ever compared/advanced as UTC, so
// gridding a calendar can't drift a day across a DST boundary. `todayISO` is the
// one place that reads the *local* clock, because "today" for a workout log is
// the user's day, not UTC's.

export function todayISO(now: Date = new Date()): string {
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  return `${now.getFullYear()}-${month}-${day}`;
}

export function parseISODate(iso: string): Date {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d));
}

export function toISODate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

export function addDays(iso: string, days: number): string {
  const date = parseISODate(iso);
  date.setUTCDate(date.getUTCDate() + days);
  return toISODate(date);
}

/** 0 = Sunday … 6 = Saturday, matching the heatmap's row order. */
export function weekdayIndex(iso: string): number {
  return parseISODate(iso).getUTCDay();
}

// --- Activity heatmap --------------------------------------------------------

export interface HeatmapDay {
  date: string;
  activityType: ActivityType;
  /** A *different* activity also happened that day — drawn as a corner mark. */
  secondary: boolean;
  durationMinutes: number | null;
  intensityRating: number | null;
}

export interface HeatmapCell {
  date: string;
  /** False for the days padding the last column past today — rendered blank. */
  inRange: boolean;
  day: HeatmapDay | null;
}

/** Which logged number drives a box's shade. Duration is the default: it's the
 *  more objective of the two to log, and the doc defers the final call until
 *  there's real data to compare (docs/lifestyle-tab.md §1). */
export type ShadeMetric = 'duration' | 'intensity';

/** Intensity is a fixed 1–10 RPE, so its shade is absolute; duration has no
 *  ceiling, so it's scaled against the busiest day on screen. */
export const INTENSITY_MAX = 10;

export function metricValue(
  day: HeatmapDay,
  metric: ShadeMetric
): number | null {
  return metric === 'intensity' ? day.intensityRating : day.durationMinutes;
}

/**
 * Bucket a value into one of four shades. A day that was logged but carries no
 * number for the chosen metric still returns 1 — something happened, and an
 * unshaded box would read as a rest day.
 */
export function shadeLevel(
  value: number | null,
  max: number
): 0 | 1 | 2 | 3 | 4 {
  if (value === null || value <= 0) return 1;
  if (max <= 0) return 1;
  const ratio = Math.min(value / max, 1);
  if (ratio <= 0.25) return 1;
  if (ratio <= 0.5) return 2;
  if (ratio <= 0.75) return 3;
  return 4;
}

/** The busiest day on screen, used as the top of the duration scale. */
export function metricCeiling(days: HeatmapDay[], metric: ShadeMetric): number {
  if (metric === 'intensity') return INTENSITY_MAX;
  return days.reduce((max, d) => Math.max(max, d.durationMinutes ?? 0), 0);
}

/** Opacity for a shade level — one hue, light to solid (never a second hue). */
export function shadeOpacity(level: 0 | 1 | 2 | 3 | 4): number {
  return [0, 0.3, 0.5, 0.75, 1][level];
}

/**
 * Lay out a GitHub-style grid: one column per week, seven rows (Sunday first),
 * ending on the column containing `endDate`.
 *
 * Days with nothing logged come back with `day: null` — the API only sends days
 * that have sessions, so the grid is built from the calendar and looked up.
 */
export function buildHeatmapGrid(
  days: HeatmapDay[],
  endDate: string,
  weekCount: number
): HeatmapCell[][] {
  const byDate = new Map(days.map(d => [d.date, d]));
  // Back up to the Sunday of the first column, so every column is a full week.
  const lastColumnStart = addDays(endDate, -weekdayIndex(endDate));
  const gridStart = addDays(lastColumnStart, -(weekCount - 1) * 7);

  const weeks: HeatmapCell[][] = [];
  for (let w = 0; w < weekCount; w++) {
    const week: HeatmapCell[] = [];
    for (let d = 0; d < 7; d++) {
      const date = addDays(gridStart, w * 7 + d);
      week.push({
        date,
        inRange: date <= endDate,
        day: byDate.get(date) ?? null,
      });
    }
    weeks.push(week);
  }
  return weeks;
}

/** Month labels for the grid header: the column each new month starts in. */
export function monthLabels(
  weeks: HeatmapCell[][]
): { column: number; label: string }[] {
  const labels: { column: number; label: string }[] = [];
  let lastMonth = '';
  weeks.forEach((week, column) => {
    const month = week[0].date.slice(0, 7);
    if (month !== lastMonth) {
      labels.push({
        column,
        label: parseISODate(week[0].date).toLocaleString('en-US', {
          month: 'short',
          timeZone: 'UTC',
        }),
      });
      lastMonth = month;
    }
  });
  return labels;
}

// --- Chart geometry ----------------------------------------------------------

export interface SeriesPoint {
  label: string;
  value: number;
}

export interface PlottedPoint extends SeriesPoint {
  x: number;
  y: number;
}

export interface Plot {
  points: PlottedPoint[];
  path: string;
  min: number;
  max: number;
}

/**
 * Project a series onto an SVG box. Hand-rolled rather than pulling in a
 * charting library — two sparklines don't justify the dependency, and this way
 * the geometry is testable in the node environment like the rest of src/lib.
 *
 * A flat series (or a single point) is centred vertically instead of dividing
 * by a zero range, so one weigh-in draws a line through the middle rather than
 * pinning to an edge.
 */
export function plotSeries(
  series: SeriesPoint[],
  width: number,
  height: number,
  pad = 4
): Plot {
  if (series.length === 0) {
    return { points: [], path: '', min: 0, max: 0 };
  }
  const values = series.map(p => p.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min;
  const innerWidth = Math.max(width - pad * 2, 0);
  const innerHeight = Math.max(height - pad * 2, 0);

  const points = series.map((point, i) => ({
    ...point,
    x:
      series.length === 1
        ? pad + innerWidth / 2
        : pad + (innerWidth * i) / (series.length - 1),
    y:
      range === 0
        ? pad + innerHeight / 2
        : pad + innerHeight * (1 - (point.value - min) / range),
  }));

  const path = points
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(2)},${p.y.toFixed(2)}`)
    .join(' ');
  return { points, path, min, max };
}

/** The plotted point nearest an x offset — the crosshair/tooltip hit test. */
export function nearestPoint(
  points: PlottedPoint[],
  x: number
): PlottedPoint | null {
  if (points.length === 0) return null;
  return points.reduce((best, p) =>
    Math.abs(p.x - x) < Math.abs(best.x - x) ? p : best
  );
}

// --- Calories ----------------------------------------------------------------

// "chicken breast and rice, ~600" / "protein shake 180 kcal" — a description
// followed by a trailing calorie count, which is how this actually gets typed.
const CALORIE_LINE =
  /^(.*?)[\s,;:~=-]*(\d{1,5})\s*(?:k?cal|kcal|calories)?\s*$/i;

export interface CalorieEntry {
  description: string;
  calories: number;
}

/**
 * Pull a description and calorie count out of one freeform line, so the whole
 * entry can be typed into a single box. Returns null when there's no trailing
 * number or nothing left to call a description — the caller then falls back to
 * the separate description/kcal fields rather than guessing.
 */
export function parseCalorieEntry(text: string): CalorieEntry | null {
  const match = CALORIE_LINE.exec(text.trim());
  if (!match) return null;
  const description = match[1].trim().replace(/[\s,;:~=-]+$/, '');
  const calories = Number(match[2]);
  if (!description || !Number.isFinite(calories)) return null;
  return { description, calories };
}
