import {
  explainPrompt,
  itemStatus,
  parseCoach,
  parseReview,
  stars,
  tally,
} from '@/lib/coachReview';

const MARK: Record<ReturnType<typeof itemStatus>, string> = {
  done: '✓',
  missed: '✗',
  excused: '☾',
  unknown: '?',
};

/** The review of yesterday on a briefing, and the coach's check-in and
 *  goal-rollover messages. `onReply` puts text in the composer: the user
 *  finishes the sentence and the agent records the reason. */
export function CoachCards({
  metadata,
  onReply,
  onSetGoals,
}: {
  metadata?: string | null;
  onReply: (text: string) => void;
  onSetGoals?: () => void;
}) {
  const review = parseReview(metadata);
  const coach = parseCoach(metadata);
  if (!review && !coach) return null;
  return (
    <div className="mt-2 space-y-2">
      {review && (
        <section
          aria-label="Yesterday's review"
          className="rounded-lg border border-white/10 p-3 text-sm"
        >
          <div className="flex items-baseline gap-2">
            <strong>Yesterday</strong>
            {review.rating && (
              <span
                className="text-orange-400"
                aria-label={`${review.rating} of 5`}
              >
                {stars(review.rating)}
              </span>
            )}
            <span className="ml-auto text-xs text-[var(--color-text-muted)]">
              {tally(review)}
            </span>
          </div>
          <ul className="mt-2 space-y-1">
            {review.items.map(item => (
              <li
                key={`${item.kind}-${item.id ?? item.title}`}
                className="flex gap-2"
              >
                <span aria-label={itemStatus(item)}>
                  {MARK[itemStatus(item)]}
                </span>
                <span>
                  {item.title}
                  {item.excuse && (
                    <span className="block text-xs text-[var(--color-text-muted)]">
                      {item.excuse}
                    </span>
                  )}
                </span>
              </li>
            ))}
          </ul>
          {review.overrideReason && review.rating !== review.computedRating && (
            <p className="mt-1 text-xs text-[var(--color-text-muted)]">
              Adjusted from {review.computedRating}★: {review.overrideReason}
            </p>
          )}
          {review.items.some(i => itemStatus(i) === 'missed') && (
            <button
              type="button"
              className="mt-2 text-[var(--color-primary)]"
              onClick={() => onReply(explainPrompt(review))}
            >
              Explain
            </button>
          )}
        </section>
      )}
      {coach?.kind === 'checkin' && coach.open.length > 0 && (
        <button
          type="button"
          className="text-sm text-[var(--color-primary)]"
          onClick={() =>
            onReply(
              `About today — ${coach.open.map(i => i.title).join(', ')}: `
            )
          }
        >
          Tell me about today
        </button>
      )}
      {coach?.kind === 'rollover' &&
        coach.horizons.length > 0 &&
        onSetGoals && (
          <button
            type="button"
            className="text-sm text-[var(--color-primary)]"
            onClick={onSetGoals}
          >
            Set this {coach.horizons.join(' and ')}'s goals
          </button>
        )}
    </div>
  );
}
