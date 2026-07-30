import { describe, expect, it } from 'vitest';
import {
  addDays,
  buildHeatmapGrid,
  isActivityType,
  metricCeiling,
  metricValue,
  monthLabels,
  nearestPoint,
  parseCalorieEntry,
  plotSeries,
  shadeLevel,
  shadeOpacity,
  todayISO,
  weekdayIndex,
  type HeatmapDay,
} from './lifestyle';

const day = (over: Partial<HeatmapDay> & { date: string }): HeatmapDay => ({
  activityType: 'outside',
  secondary: false,
  durationMinutes: null,
  intensityRating: null,
  ...over,
});

describe('dates', () => {
  it('reads today from the local clock, not UTC', () => {
    // 2026-07-30 23:30 local: a UTC-based conversion could roll to the 31st.
    expect(todayISO(new Date(2026, 6, 30, 23, 30))).toBe('2026-07-30');
    expect(todayISO(new Date(2026, 0, 5, 0, 15))).toBe('2026-01-05');
  });

  it('advances and rewinds days across month and year ends', () => {
    expect(addDays('2026-07-30', 3)).toBe('2026-08-02');
    expect(addDays('2026-01-01', -1)).toBe('2025-12-31');
    expect(addDays('2024-02-28', 1)).toBe('2024-02-29'); // leap year
  });

  it('does not drift a day across a DST boundary', () => {
    // North American DST starts 2026-03-08; stepping through it must stay exact.
    expect(addDays('2026-03-07', 1)).toBe('2026-03-08');
    expect(addDays('2026-03-08', 1)).toBe('2026-03-09');
  });

  it('indexes weekdays with Sunday first', () => {
    expect(weekdayIndex('2026-07-26')).toBe(0); // Sunday
    expect(weekdayIndex('2026-07-30')).toBe(4); // Thursday
  });
});

describe('shading', () => {
  it('buckets a value into four levels against the ceiling', () => {
    expect(shadeLevel(15, 60)).toBe(1);
    expect(shadeLevel(30, 60)).toBe(2);
    expect(shadeLevel(45, 60)).toBe(3);
    expect(shadeLevel(60, 60)).toBe(4);
  });

  it('shades a logged day with no number as level 1, never blank', () => {
    // An unshaded box would read as a rest day, which is wrong — something
    // happened, the duration just was not recorded.
    expect(shadeLevel(null, 60)).toBe(1);
    expect(shadeLevel(0, 60)).toBe(1);
    expect(shadeLevel(30, 0)).toBe(1);
  });

  it('clamps a value above the ceiling instead of overflowing', () => {
    expect(shadeLevel(500, 60)).toBe(4);
    expect(shadeOpacity(shadeLevel(500, 60))).toBe(1);
  });

  it('scales duration against the busiest day but intensity against 10', () => {
    const days = [
      day({ date: '2026-07-01', durationMinutes: 45, intensityRating: 5 }),
      day({ date: '2026-07-02', durationMinutes: 90, intensityRating: 8 }),
    ];
    expect(metricCeiling(days, 'duration')).toBe(90);
    expect(metricCeiling(days, 'intensity')).toBe(10);
  });

  it('reads the metric the toggle selected', () => {
    const d = day({
      date: '2026-07-01',
      durationMinutes: 45,
      intensityRating: 8,
    });
    expect(metricValue(d, 'duration')).toBe(45);
    expect(metricValue(d, 'intensity')).toBe(8);
  });
});

describe('buildHeatmapGrid', () => {
  const end = '2026-07-30'; // a Thursday

  it('lays out full Sunday-first weeks ending on the current column', () => {
    const weeks = buildHeatmapGrid([], end, 3);
    expect(weeks).toHaveLength(3);
    expect(weeks.every(w => w.length === 7)).toBe(true);
    expect(weeks.every(w => weekdayIndex(w[0].date) === 0)).toBe(true);
    expect(weeks[2][4].date).toBe(end); // Thursday of the last column
  });

  it('marks the days after today as out of range', () => {
    const weeks = buildHeatmapGrid([], end, 2);
    const last = weeks[1];
    expect(last.slice(0, 5).every(c => c.inRange)).toBe(true);
    expect(last.slice(5).every(c => c.inRange)).toBe(false);
  });

  it('attaches logged days and leaves the rest null', () => {
    const logged = day({
      date: '2026-07-28',
      activityType: 'goodlife_brother',
      secondary: true,
      durationMinutes: 90,
    });
    const weeks = buildHeatmapGrid([logged], end, 1);
    const cell = weeks[0].find(c => c.date === '2026-07-28');
    expect(cell?.day).toEqual(logged);
    expect(weeks[0].find(c => c.date === '2026-07-27')?.day).toBeNull();
  });

  it('ignores logged days that fall outside the grid', () => {
    const weeks = buildHeatmapGrid([day({ date: '2020-01-01' })], end, 2);
    expect(weeks.flat().every(c => c.day === null)).toBe(true);
  });

  it('labels only the column where each month starts', () => {
    const weeks = buildHeatmapGrid([], end, 6);
    const labels = monthLabels(weeks);
    expect(labels[0].column).toBe(0);
    // Six weeks back from late July spans June and July, and no month is
    // labelled twice.
    expect(new Set(labels.map(l => l.label)).size).toBe(labels.length);
  });
});

describe('plotSeries', () => {
  const series = [
    { label: 'a', value: 10 },
    { label: 'b', value: 20 },
    { label: 'c', value: 30 },
  ];

  it('spans the box and puts the largest value at the top', () => {
    const { points, min, max } = plotSeries(series, 100, 50, 0);
    expect(min).toBe(10);
    expect(max).toBe(30);
    expect(points.map(p => p.x)).toEqual([0, 50, 100]);
    expect(points[0].y).toBe(50); // smallest sits at the bottom edge
    expect(points[2].y).toBe(0);
  });

  it('insets the plot by the padding', () => {
    const { points } = plotSeries(series, 100, 50, 8);
    expect(points[0].x).toBe(8);
    expect(points[2].x).toBe(92);
    expect(points[2].y).toBe(8);
  });

  it('centres a flat series instead of dividing by a zero range', () => {
    const flat = [
      { label: 'a', value: 80 },
      { label: 'b', value: 80 },
    ];
    const { points } = plotSeries(flat, 100, 50, 0);
    expect(points.map(p => p.y)).toEqual([25, 25]);
  });

  it('centres a lone point rather than pinning it to an edge', () => {
    const { points, path } = plotSeries(
      [{ label: 'a', value: 80 }],
      100,
      50,
      0
    );
    expect(points[0]).toMatchObject({ x: 50, y: 25 });
    expect(path).toBe('M50.00,25.00');
  });

  it('returns an empty plot for an empty series', () => {
    expect(plotSeries([], 100, 50)).toEqual({
      points: [],
      path: '',
      min: 0,
      max: 0,
    });
  });

  it('builds an SVG path that starts with a move', () => {
    expect(plotSeries(series, 100, 50, 0).path).toBe(
      'M0.00,50.00 L50.00,25.00 L100.00,0.00'
    );
  });
});

describe('nearestPoint', () => {
  const { points } = plotSeries(
    [
      { label: 'a', value: 1 },
      { label: 'b', value: 2 },
      { label: 'c', value: 3 },
    ],
    100,
    50,
    0
  );

  it('finds the closest point to a pointer position', () => {
    expect(nearestPoint(points, 0)?.label).toBe('a');
    expect(nearestPoint(points, 48)?.label).toBe('b');
    expect(nearestPoint(points, 200)?.label).toBe('c');
  });

  it('returns null with nothing plotted', () => {
    expect(nearestPoint([], 10)).toBeNull();
  });
});

describe('parseCalorieEntry', () => {
  it('splits a description from a trailing count', () => {
    expect(parseCalorieEntry('chicken breast and rice, ~600')).toEqual({
      description: 'chicken breast and rice',
      calories: 600,
    });
    expect(parseCalorieEntry('protein shake 180')).toEqual({
      description: 'protein shake',
      calories: 180,
    });
  });

  it('accepts a kcal/cal unit', () => {
    expect(parseCalorieEntry('oats 320 kcal')?.calories).toBe(320);
    expect(parseCalorieEntry('oats 320cal')?.calories).toBe(320);
  });

  it('takes the last number, not one inside the description', () => {
    expect(parseCalorieEntry('2 eggs and toast 400')).toEqual({
      description: '2 eggs and toast',
      calories: 400,
    });
  });

  it('trims separator punctuation off the description', () => {
    expect(parseCalorieEntry('  burrito  -  850  ')?.description).toBe(
      'burrito'
    );
  });

  it('returns null when there is no count or no description', () => {
    expect(parseCalorieEntry('protein shake')).toBeNull();
    expect(parseCalorieEntry('600')).toBeNull();
    expect(parseCalorieEntry('~600')).toBeNull();
    expect(parseCalorieEntry('')).toBeNull();
  });
});

describe('isActivityType', () => {
  it('accepts the four types and nothing else', () => {
    expect(isActivityType('goodlife_brother')).toBe(true);
    expect(isActivityType('outside')).toBe(true);
    expect(isActivityType('gym')).toBe(false);
    expect(isActivityType(null)).toBe(false);
  });
});
