/** The coach's chat metadata (backend/coach/): the review of yesterday on a
 *  briefing, and the check-in and goal-rollover messages. Pure, so it is tested
 *  without jsdom; the same parsing is Core's `CoachReview.swift` on the phone. */

export interface ReviewItem {
  kind: string;
  id: string | null;
  title: string;
  /** null when it can't be said — a goal the judge was unsure about. */
  done: boolean | null;
  excused: boolean;
  excuse: string | null;
}

export interface DayReviewCard {
  day: string;
  rating: number | null;
  computedRating: number | null;
  overrideReason: string | null;
  done: number;
  missed: number;
  excused: number;
  items: ReviewItem[];
}

export interface GradedGoal {
  title: string;
  rating: number | null;
  periodLabel: string;
}

export type CoachMessage =
  | { kind: 'checkin'; day: string; open: ReviewItem[] }
  | { kind: 'rollover'; day: string; horizons: string[]; goals: GradedGoal[] };

function parse(
  metadata: string | null | undefined
): Record<string, unknown> | null {
  if (!metadata) return null;
  try {
    const value = JSON.parse(metadata);
    return value && typeof value === 'object' ? value : null;
  } catch {
    return null;
  }
}

export function parseReview(
  metadata: string | null | undefined
): DayReviewCard | null {
  const review = parse(metadata)?.review as DayReviewCard | undefined;
  if (!review || typeof review.day !== 'string' || !Array.isArray(review.items))
    return null;
  return review;
}

export function parseCoach(
  metadata: string | null | undefined
): CoachMessage | null {
  const coach = parse(metadata)?.coach as Record<string, unknown> | undefined;
  if (!coach || typeof coach.day !== 'string') return null;
  if (coach.kind === 'checkin') {
    return {
      kind: 'checkin',
      day: coach.day,
      open: (coach.open as ReviewItem[]) ?? [],
    };
  }
  if (coach.kind === 'rollover') {
    return {
      kind: 'rollover',
      day: coach.day,
      horizons: (coach.horizons as string[]) ?? [],
      goals: (coach.goals as GradedGoal[]) ?? [],
    };
  }
  return null;
}

export function stars(rating: number | null): string {
  return rating ? '★'.repeat(rating) + '☆'.repeat(5 - rating) : '';
}

export function itemStatus(
  item: ReviewItem
): 'done' | 'missed' | 'excused' | 'unknown' {
  if (item.excused) return 'excused';
  if (item.done === null) return 'unknown';
  return item.done ? 'done' : 'missed';
}

/** "4 of 5 done · 1 excused". */
export function tally(review: DayReviewCard): string {
  const counted = review.done + review.missed;
  if (!counted) return review.excused ? 'Excused' : 'Nothing tracked';
  return `${review.done} of ${counted} done${review.excused ? ` · ${review.excused} excused` : ''}`;
}

/** What "Explain" puts in the composer: the user finishes the sentence. */
export function explainPrompt(review: DayReviewCard): string {
  const missed = review.items
    .filter(i => itemStatus(i) === 'missed')
    .map(i => i.title);
  return missed.length
    ? `About yesterday — ${missed.join(', ')}: `
    : 'About yesterday: ';
}
