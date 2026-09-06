import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  DAY_ZOOM_LEVELS,
  DEFAULT_DAY_ZOOM,
  getStoredZoom,
  isDayZoom,
  stepZoom,
  storeZoom,
} from './calendarZoom';

describe('stepZoom', () => {
  it('walks the levels one at a time', () => {
    expect(stepZoom(1, 1)).toBe(2);
    expect(stepZoom(2, 1)).toBe(3);
    expect(stepZoom(3, -1)).toBe(2);
  });

  it('stops at each end rather than wrapping', () => {
    // A control that jumps from 3x back to 1x on one more tap is a control
    // you stop trusting.
    expect(stepZoom(3, 1)).toBe(3);
    expect(stepZoom(1, -1)).toBe(1);
    expect(stepZoom(1, 99)).toBe(DAY_ZOOM_LEVELS[DAY_ZOOM_LEVELS.length - 1]);
    expect(stepZoom(3, -99)).toBe(DAY_ZOOM_LEVELS[0]);
  });
});

describe('isDayZoom', () => {
  it('accepts only the offered levels', () => {
    expect(isDayZoom(2)).toBe(true);
    expect(isDayZoom(4)).toBe(false);
    expect(isDayZoom('2')).toBe(false);
    expect(isDayZoom(null)).toBe(false);
  });
});

describe('persistence', () => {
  // jsdom is not the default environment here, so stand in for localStorage
  // with the two methods this module touches.
  beforeEach(() => {
    const store = new Map<string, string>();
    vi.stubGlobal('localStorage', {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
    });
  });

  it('round-trips a level', () => {
    storeZoom(3);
    expect(getStoredZoom()).toBe(3);
  });

  it('falls back to the default when nothing is stored', () => {
    expect(getStoredZoom()).toBe(DEFAULT_DAY_ZOOM);
  });

  it('falls back rather than trusting a stale or hand-edited value', () => {
    storeZoom(2);
    localStorage.setItem('lunaschal:calendarDayZoom', '7');
    expect(getStoredZoom()).toBe(DEFAULT_DAY_ZOOM);
  });

  it('survives storage being unavailable', () => {
    // A private window, or site data blocked. A working day view matters more
    // than remembering the zoom.
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new Error('denied');
      },
      setItem: () => {
        throw new Error('denied');
      },
    });
    expect(getStoredZoom()).toBe(DEFAULT_DAY_ZOOM);
    expect(() => storeZoom(3)).not.toThrow();
  });
});
