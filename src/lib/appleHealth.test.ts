import { describe, expect, it } from 'vitest';
import type { HealthDay, HealthWorkout } from '@/hooks/api';
import {
  activityLabel,
  exerciseBars,
  exerciseSummary,
  syncedAgo,
  workoutDetail,
} from './appleHealth';

const day = (date: string, exerciseMinutes: number | null): HealthDay => ({
  date,
  exerciseMinutes,
  steps: null,
  activeEnergyKcal: null,
});

describe('exerciseBars', () => {
  it('keeps a slot for a day with no reading', () => {
    const { bars } = exerciseBars(
      [day('a', 10), day('b', null), day('c', 20)],
      90,
      30,
      0
    );
    expect(bars.map(b => b.x)).toEqual([0, 30, 60]);
    expect(bars[1].height).toBe(0);
  });

  it('never scales a quiet stretch above the goal line', () => {
    const { max, goalY, bars } = exerciseBars([day('a', 15)], 10, 60, 0);
    expect(max).toBe(30);
    expect(goalY).toBe(0);
    expect(bars[0].height).toBe(30);
  });

  it('scales to the busiest day when it passes the goal', () => {
    const { max, goalY } = exerciseBars([day('a', 60), day('b', 5)], 20, 60);
    expect(max).toBe(60);
    expect(goalY).toBe(30);
  });
});

describe('exerciseSummary', () => {
  it('averages over days that have a reading only', () => {
    expect(
      exerciseSummary([day('a', 20), day('b', null), day('c', 41)])
    ).toEqual({ total: 61, average: 31 });
    expect(exerciseSummary([day('a', null)])).toEqual({
      total: 0,
      average: null,
    });
  });
});

describe('labels', () => {
  it('splits HealthKit camelCase names', () => {
    expect(activityLabel('highIntensityIntervalTraining')).toBe(
      'High intensity interval training'
    );
    expect(activityLabel('walking')).toBe('Walking');
    expect(activityLabel('')).toBe('Workout');
  });

  it('leaves out what was not recorded', () => {
    const w: HealthWorkout = {
      id: 'x',
      activityType: 52,
      activityName: 'walking',
      start: 0,
      end: 2520,
      durationSeconds: 2520,
      energyKcal: 190.4,
      distanceMeters: 3400,
      source: 'Apple Watch',
    };
    expect(workoutDetail(w)).toBe('42 min · 3.4 km · 190 kcal');
    expect(
      workoutDetail({ ...w, energyKcal: null, distanceMeters: null })
    ).toBe('42 min');
  });

  it('says how long ago the phone synced', () => {
    const now = 1_000_000 * 1000;
    expect(syncedAgo(1_000_000, now)).toBe('just now');
    expect(syncedAgo(1_000_000 - 300, now)).toBe('5 min ago');
    expect(syncedAgo(1_000_000 - 3 * 3600, now)).toBe('3 h ago');
    expect(syncedAgo(1_000_000 - 5 * 86400, now)).toBe('5 days ago');
  });
});
