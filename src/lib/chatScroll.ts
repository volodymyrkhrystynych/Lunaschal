// Auto-scroll policy for the chat transcript.
//
// Kept out of the component (and free of any DOM types) so the arithmetic can
// be tested in the node environment — the browser numbers it reads are all zero
// under jsdom, which is exactly where an off-by-one in this sort of check hides.

export interface ScrollMetrics {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
}

// How close to the bottom still counts as "at the bottom". Generous on purpose:
// sub-pixel rounding on a zoomed or fractional-DPI display leaves a couple of
// pixels of slack at a genuine scroll-bottom, and a threshold of 0 would read
// that as "the user has scrolled away" and stop following the reply.
export const STICK_THRESHOLD_PX = 64;

/**
 * Is the transcript scrolled to (or near) the bottom?
 *
 * This is the whole "should the view follow new content" decision. Following
 * unconditionally is what made a streaming reply impossible to read back
 * through: every delegate step and reasoning delta yanked the view down again.
 */
export function isAtBottom(
  { scrollTop, scrollHeight, clientHeight }: ScrollMetrics,
  threshold: number = STICK_THRESHOLD_PX
): boolean {
  return scrollHeight - scrollTop - clientHeight <= threshold;
}
