import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';

// With `globals: false` Testing Library never registers its auto-cleanup, so
// rendered trees (and their window listeners) would leak into the next test.
afterEach(cleanup);

// jsdom has no matchMedia; components now call useIsMobile() on mount. Default to
// desktop (matches: false) so existing component tests render as before. Tests
// that exercise mobile behavior override window.matchMedia with their own mock.
if (typeof window !== 'undefined' && !window.matchMedia) {
  window.matchMedia = (query: string): MediaQueryList =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList;
}
