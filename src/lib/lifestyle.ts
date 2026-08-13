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

// Used to flag the sidebar when today has no selfie yet. Takes the fetched
// rows rather than re-deriving "today" itself, so the caller controls the
// clock read the same way the newspapers badge does.
export function isTodaySelfieMissing(
  selfies: { date: string }[],
  today: string
): boolean {
  return !selfies.some(s => s.date === today);
}

// Flags the sidebar when today's logged calories are under a "you probably
// haven't eaten enough" floor. 1,500 is a general baseline amount of food,
// not a diet target — this is a reminder to log/eat, not a calorie limit.
export const LOW_CALORIE_THRESHOLD = 1500;

export function isTodayCaloriesLow(totalCalories: number): boolean {
  return totalCalories < LOW_CALORIE_THRESHOLD;
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

/** Intensity is a fixed 1–5 star rating, so its shade is absolute; duration has
 *  no ceiling, so it's scaled against the busiest day on screen. */
export const INTENSITY_MAX = 5;

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

// --- Intensity ---------------------------------------------------------------

/**
 * Workout intensity is a 1–5 star rating, and **the words are the feature**.
 * A bare 1–10 RPE was too subjective to rate the same way twice, so every star
 * carries a written meaning; the picker and every readout surface it (tooltip,
 * label, screen-reader text) rather than relying on the glyph count alone.
 *
 * Indexed from 1 — `INTENSITY_LABELS[0]` is deliberately absent.
 */
export const INTENSITY_LABELS: Record<number, string> = {
  1: 'Not intense whatsoever',
  2: 'Just a smidge',
  3: "I'm sweating",
  4: "I'm really trying hard",
  5: 'I am going ham',
};

/** The written meaning of a star count, or null when it isn't 1–5. */
export function intensityLabel(
  value: number | null | undefined
): string | null {
  if (value == null) return null;
  return INTENSITY_LABELS[value] ?? null;
}

/** "★★★☆☆" — decoration only; never the sole carrier of the rating. */
export function intensityStars(value: number): string {
  const filled = Math.max(0, Math.min(Math.round(value), INTENSITY_MAX));
  return '★'.repeat(filled) + '☆'.repeat(INTENSITY_MAX - filled);
}

/** "3/5 — I'm sweating": the text form used for tooltips and aria-labels, so a
 *  screen reader never has to interpret a run of star glyphs. */
export function intensityText(value: number | null | undefined): string | null {
  if (value == null) return null;
  const label = intensityLabel(value);
  const scale = `${value}/${INTENSITY_MAX}`;
  return label ? `${scale} — ${label}` : scale;
}

// --- Workout sets ------------------------------------------------------------

export interface WorkoutSetLike {
  weight: number | null;
  reps: number | null;
}

export interface SetGroup extends WorkoutSetLike {
  /** How many identical sets in a row this group stands for. */
  count: number;
}

/** Collapse a run of identical sets — "10 10 10 10" is one thing done four
 *  times, not four things. Only *consecutive* equals fold, so a real 60/60/65
 *  progression through the session still reads in order. */
export function groupSets(sets: WorkoutSetLike[]): SetGroup[] {
  const groups: SetGroup[] = [];
  for (const s of sets) {
    const last = groups[groups.length - 1];
    if (last && last.weight === s.weight && last.reps === s.reps)
      last.count += 1;
    else groups.push({ weight: s.weight, reps: s.reps, count: 1 });
  }
  return groups;
}

/**
 * One group as text. A null weight means bodyweight — it is never rendered as
 * 0, which would read as "lifted nothing" and plot as a real data point.
 *
 *   60×8            one set, 60 kg for 8
 *   60×8 ×3         three identical weighted sets
 *   10 bodyweight   one set of 10 at bodyweight
 *   10 × 4 bodyweight   four sets of 10 at bodyweight
 */
export function formatSetGroup(group: SetGroup): string {
  const reps = group.reps ?? '?';
  if (group.weight == null) {
    return group.count > 1
      ? `${reps} × ${group.count} bodyweight`
      : `${reps} bodyweight`;
  }
  const one = `${group.weight}×${reps}`;
  return group.count > 1 ? `${one} ×${group.count}` : one;
}

/** The whole set list of an exercise as one readable line. */
export function formatSets(sets: WorkoutSetLike[]): string {
  return groupSets(sets).map(formatSetGroup).join('  ');
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
  return project(series, width, height, pad, {
    min: Math.min(...values),
    max: Math.max(...values),
  });
}

/** Project one series onto a *given* y-domain. Split out of `plotSeries` so
 *  several series can share one — see `plotMultiSeries`. */
function project(
  series: SeriesPoint[],
  width: number,
  height: number,
  pad: number,
  domain: { min: number; max: number }
): Plot {
  const { min, max } = domain;
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
  const i = nearestIndex(points, x);
  return i === -1 ? null : points[i];
}

/** Same hit test, by index — a multi-series crosshair reads one x position off
 *  every line at once, so it needs the position, not one line's point. */
export function nearestIndex(points: PlottedPoint[], x: number): number {
  if (points.length === 0) return -1;
  let best = 0;
  points.forEach((p, i) => {
    if (Math.abs(p.x - x) < Math.abs(points[best].x - x)) best = i;
  });
  return best;
}

export interface MultiPlot {
  plots: Plot[];
  min: number;
  max: number;
}

/**
 * Project several series onto **one shared y-domain**.
 *
 * A shared axis is the whole point: two lines on two scales is the dual-axis
 * chart, which can be made to show any relationship you like by choosing the
 * scales. These series are directly comparable (both are counts per week), so
 * one axis is honest and the comparison is the thing being drawn.
 *
 * `domain` overrides either end — counts pass `min: 0`, because a line chart
 * whose floor is the quietest week makes an ordinary week look like a collapse.
 * Series must be equal-length (the same weeks), which is what lets one x index
 * address all of them.
 */
export function plotMultiSeries(
  seriesList: SeriesPoint[][],
  width: number,
  height: number,
  pad = 4,
  domain: { min?: number; max?: number } = {}
): MultiPlot {
  const values = seriesList.flat().map(p => p.value);
  if (values.length === 0) {
    return {
      plots: seriesList.map(() => ({ points: [], path: '', min: 0, max: 0 })),
      min: 0,
      max: 0,
    };
  }
  const min = domain.min ?? Math.min(...values);
  const max = domain.max ?? Math.max(...values);
  return {
    plots: seriesList.map(series =>
      project(series, width, height, pad, { min, max })
    ),
    min,
    max,
  };
}

// --- Exercise progression ----------------------------------------------------

export type ProgressionMetric = 'weight' | 'volume' | 'reps';

export interface ProgressionPointLike {
  date: string;
  maxWeight: number | null;
  totalVolume: number | null;
  totalReps: number | null;
}

/**
 * Turn `/progression` points into a plottable series.
 *
 * A bodyweight exercise ("squats 10 10 10 10") has no weight on any set, so
 * both weight-based metrics are null for every point. Plotting those as 0 would
 * draw a flat line along the floor and plotting nothing would claim there was
 * no training — so the series falls back to **total reps**, and `bodyweight`
 * tells the caller to say so instead of offering a Top-set/Volume toggle that
 * can only ever be empty.
 */
export function exerciseSeries(
  points: ProgressionPointLike[],
  metric: ProgressionMetric,
  labelDate: (iso: string) => string = iso => iso
): { series: SeriesPoint[]; bodyweight: boolean; metric: ProgressionMetric } {
  const bodyweight =
    points.length > 0 && points.every(p => p.maxWeight == null);
  const effective: ProgressionMetric = bodyweight ? 'reps' : metric;
  const pick = (p: ProgressionPointLike) =>
    effective === 'weight'
      ? p.maxWeight
      : effective === 'volume'
        ? p.totalVolume
        : p.totalReps;

  const series = points
    // A missing number is an absent point, not a zero.
    .filter(p => pick(p) != null && (pick(p) as number) > 0)
    .map(p => ({ label: labelDate(p.date), value: pick(p) as number }));
  return { series, bodyweight, metric: effective };
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
