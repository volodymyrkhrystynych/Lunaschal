// @vitest-environment jsdom
import { describe, it, expect, beforeEach } from 'vitest';
import {
  NETWORK_CODE_SHELF_LIFE_MS,
  saveCachedCode,
  loadCachedCode,
  clearCachedCode,
  cacheExpiresInDays,
} from './networkCode';

const KEY = 'lunaschal:networkCode';
const T0 = 1_700_000_000_000; // fixed "now" for deterministic tests

beforeEach(() => {
  localStorage.clear();
});

describe('saveCachedCode / loadCachedCode', () => {
  it('returns null when nothing is stored', () => {
    expect(loadCachedCode(T0)).toBeNull();
  });

  it('round-trips a freshly saved code', () => {
    saveCachedCode('123456', T0);
    expect(loadCachedCode(T0)).toBe('123456');
  });

  it('returns the code anywhere inside the shelf-life window', () => {
    saveCachedCode('123456', T0);
    const almostAWeek = T0 + NETWORK_CODE_SHELF_LIFE_MS - 1;
    expect(loadCachedCode(almostAWeek)).toBe('123456');
  });

  it('returns null and clears storage once the code is stale', () => {
    saveCachedCode('123456', T0);
    const expired = T0 + NETWORK_CODE_SHELF_LIFE_MS + 1;
    expect(loadCachedCode(expired)).toBeNull();
    // stale entry should have been removed as a side effect
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it('treats a future savedAt (clock skew) as stale', () => {
    saveCachedCode('123456', T0);
    expect(loadCachedCode(T0 - 1000)).toBeNull();
  });

  it('returns null for corrupt JSON', () => {
    localStorage.setItem(KEY, 'not-json');
    expect(loadCachedCode(T0)).toBeNull();
  });

  it('returns null for a wrong-shaped object', () => {
    localStorage.setItem(KEY, JSON.stringify({ code: 123, savedAt: 'x' }));
    expect(loadCachedCode(T0)).toBeNull();
  });

  it('re-saving restarts the shelf-life clock', () => {
    saveCachedCode('123456', T0);
    const later = T0 + NETWORK_CODE_SHELF_LIFE_MS - 1;
    saveCachedCode('123456', later);
    // A point that would have been stale relative to T0 is still fresh now.
    expect(loadCachedCode(later + NETWORK_CODE_SHELF_LIFE_MS - 1)).toBe(
      '123456'
    );
  });
});

describe('clearCachedCode', () => {
  it('removes a stored code', () => {
    saveCachedCode('123456', T0);
    clearCachedCode();
    expect(loadCachedCode(T0)).toBeNull();
  });
});

describe('cacheExpiresInDays', () => {
  it('is null when nothing is stored', () => {
    expect(cacheExpiresInDays(T0)).toBeNull();
  });

  it('reports 7 days right after saving', () => {
    saveCachedCode('123456', T0);
    expect(cacheExpiresInDays(T0)).toBe(7);
  });

  it('rounds up so the last partial day reads as 1', () => {
    saveCachedCode('123456', T0);
    const halfDayLeft = T0 + NETWORK_CODE_SHELF_LIFE_MS - 12 * 60 * 60 * 1000;
    expect(cacheExpiresInDays(halfDayLeft)).toBe(1);
  });

  it('is null once expired', () => {
    saveCachedCode('123456', T0);
    expect(cacheExpiresInDays(T0 + NETWORK_CODE_SHELF_LIFE_MS + 1)).toBeNull();
  });
});
