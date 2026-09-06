// Single source of truth for the mobile breakpoint. 768px is Tailwind's default
// `md` min-width, so MOBILE_QUERY is the exact complement of `md:` utilities:
// JS-driven layout structure and CSS-driven styling stay in lockstep.
export const MOBILE_MAX_WIDTH = 767;
export const MOBILE_QUERY = `(max-width: ${MOBILE_MAX_WIDTH}px)`;

// "Big enough for two panes side by side" — what the Study desk needs.
// Deliberately not a nav gate: `desktopOnly` in Sidebar's navItems decides
// which tabs a *device* gets, and the GPD Pocket 2 satisfies it while being
// exactly the machine a split reading desk is unusable on. So Study asks this
// question itself and shows its library alone when the answer is no (see
// src/components/Study/Study.tsx) rather than vanishing from the sidebar.
// Tailwind's `lg` min-width, so JS gating and `lg:` utilities agree the way
// MOBILE_QUERY and `md:` already do.
export const LARGE_MIN_WIDTH = 1024;
export const LARGE_QUERY = `(min-width: ${LARGE_MIN_WIDTH}px)`;
