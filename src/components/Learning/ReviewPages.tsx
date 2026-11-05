import type { LearningReviewPages } from '../../hooks/api';

/** A review card's front: the pages of the entry it was saved from, stacked
 * in one column to be read top to bottom. There is no back side. */
export function ReviewPages({
  review,
}: {
  review: LearningReviewPages | undefined;
}) {
  const pages = review?.pages ?? [];
  return (
    <div className="space-y-3">
      {review?.title && (
        <div className="text-center text-lg font-medium text-[var(--color-text)]">
          {review.title}
        </div>
      )}
      {pages.length === 0 ? (
        <div className="text-center text-sm text-[var(--color-text-muted)] py-8">
          This entry's pages haven't arrived yet. They upload after the entry is
          saved.
        </div>
      ) : (
        pages.map((page, i) => (
          <img
            key={page.id}
            src={page.url}
            alt={`Page ${i + 1} of ${pages.length}`}
            loading="lazy"
            className="w-full h-auto rounded-md border border-white/10 bg-white"
          />
        ))
      )}
    </div>
  );
}
