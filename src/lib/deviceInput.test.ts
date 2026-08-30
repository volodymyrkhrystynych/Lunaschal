import { describe, it, expect, vi, afterEach } from 'vitest';
import { isTouchDevice } from './deviceInput';

function withMatchMedia(impl: unknown) {
  vi.stubGlobal('window', { matchMedia: impl });
}

afterEach(() => vi.unstubAllGlobals());

describe('isTouchDevice', () => {
  it('is true when the primary pointer is coarse', () => {
    withMatchMedia((q: string) => ({ matches: q === '(pointer: coarse)' }));
    expect(isTouchDevice()).toBe(true);
  });

  it('is false on a device with a mouse', () => {
    withMatchMedia(() => ({ matches: false }));
    expect(isTouchDevice()).toBe(false);
  });

  it('asks about the *primary* pointer, so a touchscreen laptop is not a phone', () => {
    const seen: string[] = [];
    withMatchMedia((q: string) => {
      seen.push(q);
      return { matches: false };
    });
    isTouchDevice();
    // `any-pointer: coarse` would be true of a laptop with a touchscreen, which
    // still has a mouse and should get the desktop affordances.
    expect(seen).toEqual(['(pointer: coarse)']);
  });

  it('is false rather than throwing where matchMedia is missing', () => {
    // jsdom and the PyWebView shell are both allowed not to implement it, and a
    // crash here would take the whole compose box down.
    withMatchMedia(undefined);
    expect(isTouchDevice()).toBe(false);
  });

  it('is false rather than throwing when matchMedia throws', () => {
    withMatchMedia(() => {
      throw new Error('not supported');
    });
    expect(isTouchDevice()).toBe(false);
  });
});
