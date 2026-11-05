import { describe, expect, it } from 'vitest';
import type { Goal, GoalPeriod } from '@/hooks/api';
import { horizonHeading, streakLine, unsetPrompt } from './goals';

const period = (horizon: GoalPeriod['horizon'], label: string): GoalPeriod => ({
  horizon,
  label,
  periodStart: '2026-11-02',
  periodEnd: '2026-11-08',
  goals: [],
  unset: true,
});

describe('goal helpers', () => {
  it('names horizons and unset periods', () => {
    expect(horizonHeading('quarter')).toBe('This quarter');
    expect(unsetPrompt(period('week', 'Week of Nov 2'))).toBe(
      'No goals for this week yet.'
    );
    expect(unsetPrompt(period('month', 'November 2026'))).toBe(
      'No goals for November 2026 yet.'
    );
  });

  it('describes a streak only for goals with a rule', () => {
    const goal = {
      hasRule: true,
      targetDays: 7,
      currentStreak: 3,
      status: 'active',
    } as Goal;
    expect(streakLine(goal)).toBe('3/7 consecutive days · In progress');
    expect(
      streakLine({ ...goal, hasRule: false, targetDays: null })
    ).toBeNull();
  });
});
