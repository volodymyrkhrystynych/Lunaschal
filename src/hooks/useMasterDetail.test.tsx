// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useMasterDetail } from './useMasterDetail';

function setMobile(isMobile: boolean) {
  const original = window.matchMedia;
  window.matchMedia = ((query: string) => ({
    matches: isMobile,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia;
  return () => {
    window.matchMedia = original;
  };
}

describe('useMasterDetail', () => {
  let restore: (() => void) | null = null;

  afterEach(() => {
    restore?.();
    restore = null;
  });

  it('shows both panes on desktop regardless of pane state', () => {
    restore = setMobile(false);
    const { result } = renderHook(() => useMasterDetail());
    expect(result.current.showList).toBe(true);
    expect(result.current.showDetail).toBe(true);

    act(() => result.current.openDetail());
    expect(result.current.showList).toBe(true);
    expect(result.current.showDetail).toBe(true);
  });

  it('shows exactly one pane on mobile and flips between them', () => {
    restore = setMobile(true);
    const { result } = renderHook(() => useMasterDetail());
    // Defaults to the list.
    expect(result.current.showList).toBe(true);
    expect(result.current.showDetail).toBe(false);

    act(() => result.current.openDetail());
    expect(result.current.showList).toBe(false);
    expect(result.current.showDetail).toBe(true);

    act(() => result.current.openList());
    expect(result.current.showList).toBe(true);
    expect(result.current.showDetail).toBe(false);
  });
});
