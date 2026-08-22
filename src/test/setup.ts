import { afterEach, beforeEach } from 'vitest';
import { cleanup } from '@testing-library/react';
import { onlineManager } from '@tanstack/react-query';

// With `globals: false` Testing Library never registers its auto-cleanup, so
// rendered trees (and their window listeners) would leak into the next test.
afterEach(cleanup);

// Backend reachability is module-global state, and a failed request now writes
// to it (src/offline/onlineManager.ts's reportFetchOutcome) — so one test whose
// fetch mock rejects would leave every later test in the file believing the app
// is offline, which is invisible until something like a disabled mic button
// makes it visible. Each test starts online.
beforeEach(() => onlineManager.setOnline(true));

// jsdom has no matchMedia; components now call useIsMobile() on mount. Default to
// desktop (matches: false) so existing component tests render as before. Tests
// that exercise mobile behavior override window.matchMedia with their own mock.
// jsdom implements no layout, so Element.prototype.scrollIntoView is missing
// entirely — any component that keeps a transcript scrolled to the bottom (the
// Ideas discussion) throws on mount without this. A no-op is the right stub:
// there is nothing to scroll in a headless DOM.
if (typeof Element !== 'undefined' && !Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}

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
