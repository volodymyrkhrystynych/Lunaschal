import { useEffect, useState } from 'react';
import { LARGE_QUERY, MOBILE_QUERY } from '@/lib/breakpoints';

/**
 * Subscribe to a CSS media query. Seeded synchronously from `matchMedia` so the
 * very first render is already correct — callers (e.g. the sidebar default) rely
 * on this to avoid a mobile drawer flashing open on load.
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(
    () => window.matchMedia(query).matches
  );

  useEffect(() => {
    const mql = window.matchMedia(query);
    const onChange = (e: MediaQueryListEvent) => setMatches(e.matches);
    // Re-sync in case the query changed between render and effect.
    setMatches(mql.matches);
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, [query]);

  return matches;
}

/** True on phone-width viewports (< 768px). */
export function useIsMobile(): boolean {
  return useMediaQuery(MOBILE_QUERY);
}

/** True on viewports wide enough for a two-pane view (>= 1024px). */
export function useIsLargeScreen(): boolean {
  return useMediaQuery(LARGE_QUERY);
}
