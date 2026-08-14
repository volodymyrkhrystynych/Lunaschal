import { describe, expect, it } from 'vitest';
import {
  ACTIVITY_COLORS,
  ACTIVITY_LABELS,
  ACTIVITY_TYPES,
  addDays,
  buildHeatmapGrid,
  isActivityType,
  metricCeiling,
  metricValue,
  monthLabels,
  nearestIndex,
  nearestPoint,
  exerciseSeries,
  formatSets,
  groupSets,
  INTENSITY_MAX,
  intensityLabel,
  intensityStars,
  intensityText,
  isTodayCaloriesLow,
  isTodaySelfieMissing,
  parseCalorieEntry,
  plotMultiSeries,
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
  });

  it('rolls the day over at 4am, not midnight', () => {
    // 00:15 local is still "yesterday" until 4am ticks over.
    expect(todayISO(new Date(2026, 0, 5, 0, 15))).toBe('2026-01-04');
    expect(todayISO(new Date(2026, 0, 5, 4, 0))).toBe('2026-01-05');
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

describe('isTodaySelfieMissing', () => {
  it('is false when today is in the list', () => {
    expect(
      isTodaySelfieMissing(
        [{ date: '2026-08-02' }, { date: '2026-08-03' }],
        '2026-08-03'
      )
    ).toBe(false);
  });

  it('is true when today is absent', () => {
    expect(isTodaySelfieMissing([{ date: '2026-08-02' }], '2026-08-03')).toBe(
      true
    );
  });

  it('is true for an empty list', () => {
    expect(isTodaySelfieMissing([], '2026-08-03')).toBe(true);
  });
});

describe('isTodayCaloriesLow', () => {
  it('flags totals under the 1,500 floor', () => {
    expect(isTodayCaloriesLow(0)).toBe(true);
    expect(isTodayCaloriesLow(1499)).toBe(true);
  });

  it('does not flag totals at or above the floor', () => {
    expect(isTodayCaloriesLow(1500)).toBe(false);
    expect(isTodayCaloriesLow(2200)).toBe(false);
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

  it('scales duration against the busiest day but intensity against 5 stars', () => {
    const days = [
      day({ date: '2026-07-01', durationMinutes: 45, intensityRating: 3 }),
      day({ date: '2026-07-02', durationMinutes: 90, intensityRating: 4 }),
    ];
    expect(metricCeiling(days, 'duration')).toBe(90);
    expect(metricCeiling(days, 'intensity')).toBe(5);
  });

  it('reads the metric the toggle selected', () => {
    const d = day({
      date: '2026-07-01',
      durationMinutes: 45,
      intensityRating: 4,
    });
    expect(metricValue(d, 'duration')).toBe(45);
    expect(metricValue(d, 'intensity')).toBe(4);
  });

  it('shades a 5-star day solid and a 1-star day faintly', () => {
    // The old 1-10 scale meant a hard session (8) only reached level 4 at 10;
    // on the star scale the top of the scale is actually reachable.
    expect(shadeLevel(5, 5)).toBe(4);
    expect(shadeLevel(1, 5)).toBe(1);
    expect(shadeLevel(3, 5)).toBe(3);
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

  it('reports the index, so a multi-series crosshair can read every line', () => {
    expect(nearestIndex(points, 0)).toBe(0);
    expect(nearestIndex(points, 48)).toBe(1);
    expect(nearestIndex(points, 200)).toBe(2);
    expect(nearestIndex([], 10)).toBe(-1);
  });
});

describe('plotMultiSeries', () => {
  const a = [
    { label: 'w1', value: 0 },
    { label: 'w2', value: 4 },
  ];
  const b = [
    { label: 'w1', value: 2 },
    { label: 'w2', value: 8 },
  ];

  it('projects every series onto one shared domain', () => {
    // The shared axis is the point: 4 on the first line must sit at the same
    // height as 4 on the second, which two per-series domains would break.
    const { plots, min, max } = plotMultiSeries([a, b], 100, 50, 0);
    expect([min, max]).toEqual([0, 8]);
    expect(plots[0].points[1].y).toBe(25); // 4 of 8, halfway up
    expect(plots[1].points[1].y).toBe(0); // 8 of 8, at the top
    expect(plots[1].points[0].y).toBe(37.5);
  });

  it('honours a domain floor so a quiet week is not the baseline', () => {
    const { min, plots } = plotMultiSeries(
      [
        [
          { label: 'w1', value: 6 },
          { label: 'w2', value: 8 },
        ],
      ],
      100,
      50,
      0,
      { min: 0 }
    );
    expect(min).toBe(0);
    expect(plots[0].points[0].y).toBe(12.5); // 6 of 8, not the floor
  });

  it('centres a flat shared domain instead of dividing by zero', () => {
    const flat = [
      { label: 'w1', value: 0 },
      { label: 'w2', value: 0 },
    ];
    const { plots } = plotMultiSeries([flat], 100, 50, 0, { min: 0 });
    expect(plots[0].points.map(p => p.y)).toEqual([25, 25]);
  });

  it('returns one empty plot per series when there is no data', () => {
    const { plots, min, max } = plotMultiSeries([[], []], 100, 50);
    expect(plots).toHaveLength(2);
    expect(plots.every(pl => pl.points.length === 0)).toBe(true);
    expect([min, max]).toEqual([0, 0]);
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
  it('accepts the known types and nothing else', () => {
    expect(isActivityType('goodlife_brother')).toBe(true);
    expect(isActivityType('lifting_home')).toBe(true);
    expect(isActivityType('outside')).toBe(true);
    expect(isActivityType('gym')).toBe(false);
    expect(isActivityType(null)).toBe(false);
  });

  it('gives every type exactly one label and one colour', () => {
    // A type with no colour renders a transparent heatmap cell, which reads as
    // a rest day; two types sharing one is a chart that lies.
    expect(Object.keys(ACTIVITY_LABELS).sort()).toEqual(
      [...ACTIVITY_TYPES].sort()
    );
    expect(Object.keys(ACTIVITY_COLORS).sort()).toEqual(
      [...ACTIVITY_TYPES].sort()
    );
    const hues = ACTIVITY_TYPES.map(t => ACTIVITY_COLORS[t]);
    expect(new Set(hues).size).toBe(ACTIVITY_TYPES.length);
  });

  it('orders lifting at home below the gym and above going outside', () => {
    // Index is priority: it decides which colour a mixed day's box takes.
    const order = [...ACTIVITY_TYPES];
    expect(order.indexOf('building')).toBeLessThan(
      order.indexOf('lifting_home')
    );
    expect(order.indexOf('lifting_home')).toBeLessThan(
      order.indexOf('outside')
    );
  });
});

describe('intensity', () => {
  it('is a five-point scale where every star has a written meaning', () => {
    expect(INTENSITY_MAX).toBe(5);
    expect(intensityLabel(1)).toBe('Not intense whatsoever');
    expect(intensityLabel(2)).toBe('Just a smidge');
    expect(intensityLabel(3)).toBe("I'm sweating");
    expect(intensityLabel(4)).toBe("I'm really trying hard");
    expect(intensityLabel(5)).toBe('I am going ham');
  });

  it('has no label outside 1-5', () => {
    expect(intensityLabel(null)).toBeNull();
    expect(intensityLabel(undefined)).toBeNull();
    expect(intensityLabel(0)).toBeNull();
    expect(intensityLabel(7)).toBeNull();
  });

  it('draws stars only as decoration, filled to the rating', () => {
    expect(intensityStars(3)).toBe('★★★☆☆');
    expect(intensityStars(5)).toBe('★★★★★');
    expect(intensityStars(0)).toBe('☆☆☆☆☆');
    // A stale out-of-range value from before the migration must not blow up
    // the string length.
    expect(intensityStars(9)).toBe('★★★★★');
  });

  it('spells the rating out for tooltips and screen readers', () => {
    expect(intensityText(3)).toBe("3/5 — I'm sweating");
    expect(intensityText(null)).toBeNull();
    // Out of range: still readable, just unlabelled.
    expect(intensityText(8)).toBe('8/5');
  });
});

describe('set formatting', () => {
  it('collapses a run of identical sets', () => {
    expect(
      groupSets([
        { weight: null, reps: 10 },
        { weight: null, reps: 10 },
        { weight: null, reps: 10 },
        { weight: null, reps: 10 },
      ])
    ).toEqual([{ weight: null, reps: 10, count: 4 }]);
  });

  it('keeps a progression through the session in order', () => {
    expect(
      groupSets([
        { weight: 60, reps: 8 },
        { weight: 60, reps: 8 },
        { weight: 65, reps: 6 },
      ])
    ).toEqual([
      { weight: 60, reps: 8, count: 2 },
      { weight: 65, reps: 6, count: 1 },
    ]);
  });

  it('renders bodyweight sets as bodyweight, never as 0', () => {
    // "squats 10 10 10 10"
    const sets = Array.from({ length: 4 }, () => ({ weight: null, reps: 10 }));
    expect(formatSets(sets)).toBe('10 × 4 bodyweight');
    expect(formatSets(sets)).not.toContain('0×');
  });

  it('renders a single bodyweight set without a set count', () => {
    expect(formatSets([{ weight: null, reps: 8 }])).toBe('8 bodyweight');
  });

  it('renders weighted sets with their weight', () => {
    expect(
      formatSets([
        { weight: 60, reps: 8 },
        { weight: 60, reps: 8 },
        { weight: 65, reps: 6 },
      ])
    ).toBe('60×8 ×2  65×6');
  });

  it('survives a set with no reps recorded', () => {
    expect(formatSets([{ weight: 60, reps: null }])).toBe('60×?');
    expect(formatSets([{ weight: null, reps: null }])).toBe('? bodyweight');
  });

  it('is empty for an exercise with no sets', () => {
    expect(formatSets([])).toBe('');
  });
});

describe('exerciseSeries', () => {
  const point = (
    date: string,
    maxWeight: number | null,
    totalVolume: number | null,
    totalReps: number | null
  ) => ({ date, maxWeight, totalVolume, totalReps });

  it('plots top-set weight for a weighted exercise', () => {
    const { series, bodyweight, metric } = exerciseSeries(
      [point('2026-07-01', 65, 828, 22), point('2026-07-08', 70, 350, 5)],
      'weight'
    );
    expect(bodyweight).toBe(false);
    expect(metric).toBe('weight');
    expect(series.map(p => p.value)).toEqual([65, 70]);
  });

  it('falls back to reps when nothing was ever loaded', () => {
    // A bodyweight-only exercise has no weight on any set; a weight chart would
    // otherwise be empty or a flat line at zero.
    const { series, bodyweight, metric } = exerciseSeries(
      [
        point('2026-07-01', null, null, 30),
        point('2026-07-08', null, null, 48),
      ],
      'weight'
    );
    expect(bodyweight).toBe(true);
    expect(metric).toBe('reps');
    expect(series.map(p => p.value)).toEqual([30, 48]);
  });

  it('still charts weight once a bodyweight exercise gets loaded', () => {
    const { bodyweight, metric } = exerciseSeries(
      [point('2026-07-01', null, null, 30), point('2026-07-08', 20, 200, 10)],
      'weight'
    );
    expect(bodyweight).toBe(false);
    expect(metric).toBe('weight');
  });

  it('drops days with no number rather than plotting them at zero', () => {
    const { series } = exerciseSeries(
      [point('2026-07-01', 60, 480, 8), point('2026-07-08', null, null, 12)],
      'weight'
    );
    expect(series.map(p => p.value)).toEqual([60]);
  });

  it("formats labels through the caller's formatter", () => {
    const { series } = exerciseSeries(
      [point('2026-07-01', 60, 480, 8)],
      'weight',
      iso => iso.slice(5)
    );
    expect(series[0].label).toBe('07-01');
  });

  it('is empty, and not bodyweight, with no points at all', () => {
    const { series, bodyweight } = exerciseSeries([], 'weight');
    expect(series).toEqual([]);
    expect(bodyweight).toBe(false);
  });
});
