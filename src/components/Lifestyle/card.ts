/**
 * The Lifestyle tab's card shell — one string, because the tab is a column of
 * cards and six hand-copied class lists is how they drift apart.
 *
 * `flex flex-col min-w-0` matters in the paired rows: grid children default to
 * a min-width of their content, so a wide child (a session's exercise line, a
 * chart) would push its column past the grid instead of scrolling inside it.
 */
export const CARD =
  'p-4 rounded-lg bg-[var(--color-surface)] border border-white/10 flex flex-col min-w-0';

/** Separates two stacked sections inside one card (activity/momentum, daily
 *  tasks/to-dos) — a rule they share reads as one card, two cards read as two. */
export const CARD_DIVIDER = 'mt-4 pt-4 border-t border-white/10';
