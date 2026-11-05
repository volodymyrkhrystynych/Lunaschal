import type { Goal, GoalPeriod } from '@/hooks/api';

export const GOAL_STATUS_LABEL: Record<Goal['status'], string> = {
  active: 'In progress',
  earned: 'Reward earned',
  redeemed: 'Reward redeemed',
  cancelled: 'Cancelled',
  needs_review: 'Evidence needs review',
  ended: 'Ended',
};

/** "This week", "This quarter"… — the section heading for a horizon. */
export function horizonHeading(horizon: GoalPeriod['horizon']): string {
  return `This ${horizon}`;
}

/** The prompt shown when a period has no goal open yet. */
export function unsetPrompt(period: GoalPeriod): string {
  return `No goals for ${period.horizon === 'week' ? 'this week' : period.label} yet.`;
}

/** One line of a criteria goal's progress, e.g. "3/7 consecutive days · In progress". */
export function streakLine(goal: Goal): string | null {
  if (!goal.hasRule || goal.targetDays == null) return null;
  return `${goal.currentStreak}/${goal.targetDays} consecutive days · ${GOAL_STATUS_LABEL[goal.status]}`;
}
