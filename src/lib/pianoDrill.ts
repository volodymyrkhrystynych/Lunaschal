import type { PianoDailyExercise } from './piano';

/**
 * The chained key-press drill: each exercise falls, is played, and falls again until
 * it has been played this many times in a row with no wrong key press — or until its
 * allotted minutes are spent. Mastery is measured on key presses alone, so releasing
 * a note early to reposition the hand costs nothing.
 */
export const CLEAN_RUNS_REQUIRED = 3;

export type DrillOutcome = 'repeat' | 'mastered' | 'timeUp';

/** A run with any wrong key press resets the streak; three in a row is the bar. */
export function nextStreak(streak: number, wrongNotes: number): number {
  return wrongNotes > 0 ? 0 : streak + 1;
}

/**
 * What happens after a run finishes. The budget is only ever read here, at a run
 * boundary, so an overrun never cuts a run in half — it ends the exercise after the
 * one being played. Mastery wins a tie: a run that both earns the third clean pass
 * and overruns the budget is a success, not a timeout.
 */
export function runOutcome(args: {
  streak: number;
  practicedSeconds: number;
  budgetSeconds: number;
}): DrillOutcome {
  if (args.streak >= CLEAN_RUNS_REQUIRED) return 'mastered';
  if (args.budgetSeconds > 0 && args.practicedSeconds >= args.budgetSeconds)
    return 'timeUp';
  return 'repeat';
}

/** The keys-group exercises still to play, in the order the day planned them. */
export function buildDrillQueue(
  exercises: PianoDailyExercise[]
): PianoDailyExercise[] {
  return exercises.filter(
    exercise => exercise.group === 'keys' && !exercise.completedAt
  );
}

/** Streak dots for one exercise, e.g. "●●○". */
export function streakDots(streak: number): string {
  const filled = Math.max(0, Math.min(CLEAN_RUNS_REQUIRED, streak));
  return '●'.repeat(filled) + '○'.repeat(CLEAN_RUNS_REQUIRED - filled);
}

/** "4 / 6 min" — what the block header shows against an exercise's budget. */
export function formatSpent(practicedSeconds: number, minutes: number): string {
  return `${Math.floor(practicedSeconds / 60)} / ${minutes} min`;
}
