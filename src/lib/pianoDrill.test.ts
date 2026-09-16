import { describe, expect, it } from 'vitest';
import type { PianoDailyExercise } from './piano';
import {
  CLEAN_RUNS_REQUIRED,
  buildDrillQueue,
  formatSpent,
  nextStreak,
  runOutcome,
  streakDots,
} from './pianoDrill';

function exercise(
  overrides: Partial<PianoDailyExercise> & { id: string }
): PianoDailyExercise {
  return {
    exerciseKey: 'five-finger',
    title: 'Five-finger warm-up',
    category: 'Warm-up',
    style: 'shared',
    description: '',
    instructions: '',
    group: 'keys',
    keyName: 'C',
    targetTempo: 80,
    minutes: 5,
    gradeable: true,
    pianoPieceId: null,
    measureStart: null,
    measureEnd: null,
    completedAt: null,
    cleanStreak: 0,
    practicedSeconds: 0,
    latestAttempt: null,
    ...overrides,
  };
}

describe('piano drill', () => {
  it('resets the streak on any wrong key press', () => {
    expect(nextStreak(0, 0)).toBe(1);
    expect(nextStreak(2, 0)).toBe(CLEAN_RUNS_REQUIRED);
    expect(nextStreak(2, 1)).toBe(0);
  });

  it('repeats until three clean runs in a row', () => {
    const budgetSeconds = 300;
    expect(runOutcome({ streak: 1, practicedSeconds: 30, budgetSeconds })).toBe(
      'repeat'
    );
    expect(runOutcome({ streak: 2, practicedSeconds: 30, budgetSeconds })).toBe(
      'repeat'
    );
    expect(runOutcome({ streak: 3, practicedSeconds: 30, budgetSeconds })).toBe(
      'mastered'
    );
  });

  it('gives up on time once the exercise has had its minutes', () => {
    expect(
      runOutcome({ streak: 1, practicedSeconds: 300, budgetSeconds: 300 })
    ).toBe('timeUp');
    // Mastery wins the tie: a run that earns the third clean pass is a success
    // even when it also spends the last of the budget.
    expect(
      runOutcome({ streak: 3, practicedSeconds: 900, budgetSeconds: 300 })
    ).toBe('mastered');
    // A budget of zero is no budget at all, not an instant timeout.
    expect(
      runOutcome({ streak: 0, practicedSeconds: 60, budgetSeconds: 0 })
    ).toBe('repeat');
  });

  it('queues only unfinished keys exercises, in plan order', () => {
    const queue = buildDrillQueue([
      exercise({ id: 'a' }),
      exercise({ id: 'b', completedAt: '2026-09-15T10:00:00+00:00' }),
      exercise({ id: 'c', group: 'ear' }),
      exercise({ id: 'd', group: 'repertoire' }),
      exercise({ id: 'e' }),
    ]);
    expect(queue.map(item => item.id)).toEqual(['a', 'e']);
  });

  it('renders progress the way the cards read it', () => {
    expect(streakDots(0)).toBe('○○○');
    expect(streakDots(2)).toBe('●●○');
    expect(streakDots(9)).toBe('●●●');
    expect(formatSpent(0, 6)).toBe('0 / 6 min');
    expect(formatSpent(250, 6)).toBe('4 / 6 min');
  });
});
