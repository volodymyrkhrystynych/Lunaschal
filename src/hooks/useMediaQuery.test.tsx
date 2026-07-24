// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useIsMobile, useMediaQuery } from './useMediaQuery';

// Controllable matchMedia mock: flip `matches` and fire the change listeners.
function installMatchMedia(initial: boolean) {
  const listeners = new Set<(e: MediaQueryListEvent) => void>();
  const state = { matches: initial };
  const mql = {
    get matches() {
      return state.matches;
    },
    media: '(max-width: 767px)',
    addEventListener: (_: string, cb: (e: MediaQueryListEvent) => void) =>
      listeners.add(cb),
    removeEventListener: (_: string, cb: (e: MediaQueryListEvent) => void) =>
      listeners.delete(cb),
  };
  const original = window.matchMedia;
  window.matchMedia = (() => mql) as unknown as typeof window.matchMedia;
  return {
    set(next: boolean) {
      state.matches = next;
      listeners.forEach(cb => cb({ matches: next } as MediaQueryListEvent));
    },
    restore() {
      window.matchMedia = original;
    },
  };
}

describe('useMediaQuery / useIsMobile', () => {
  let mq: ReturnType<typeof installMatchMedia> | null = null;

  afterEach(() => {
    mq?.restore();
    mq = null;
  });

  it('seeds synchronously from matchMedia on first render', () => {
    mq = installMatchMedia(true);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(true);
  });

  it('re-renders when the query starts matching', () => {
    mq = installMatchMedia(false);
    const { result } = renderHook(() => useMediaQuery('(max-width: 767px)'));
    expect(result.current).toBe(false);

    act(() => mq!.set(true));
    expect(result.current).toBe(true);

    act(() => mq!.set(false));
    expect(result.current).toBe(false);
  });
});
