// Single source of truth for the mobile breakpoint. 768px is Tailwind's default
// `md` min-width, so MOBILE_QUERY is the exact complement of `md:` utilities:
// JS-driven layout structure and CSS-driven styling stay in lockstep.
export const MOBILE_MAX_WIDTH = 767;
export const MOBILE_QUERY = `(max-width: ${MOBILE_MAX_WIDTH}px)`;
