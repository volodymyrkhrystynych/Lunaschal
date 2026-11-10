import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  baseRevision,
  clearConflict,
  documentKey,
  heldConflict,
  holdConflict,
  rememberRevision,
  resetRevision,
} from './writingDocuments';

const store = new Map<string, string>();

beforeEach(() => {
  store.clear();
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => void store.set(key, value),
    removeItem: (key: string) => void store.delete(key),
  });
});

describe('baseRevision', () => {
  it('uses the captured base until the page has seen a newer one', () => {
    const key = documentKey('chapter', 'first');
    expect(baseRevision(key, 4)).toBe(4);
    rememberRevision(key, 5);
    expect(baseRevision(key, 4)).toBe(5);
  });

  it('lets autosaves queued offline replay one after another', () => {
    const key = documentKey('chapter', 'queued');
    const queued = [3, 3, 3];
    const sent = queued.map((captured, index) => {
      const base = baseRevision(key, captured);
      rememberRevision(key, 4 + index);
      return base;
    });
    expect(sent).toEqual([3, 4, 5]);
  });

  it('sends no base when none is known, which keeps the old behaviour', () => {
    expect(baseRevision(documentKey('note', 'unknown'), undefined)).toBe(
      undefined
    );
  });

  it('can be moved back to the number the server gives', () => {
    const key = documentKey('note', 'reset');
    rememberRevision(key, 9);
    resetRevision(key, 2);
    expect(baseRevision(key, undefined)).toBe(2);
  });
});

describe('held conflicts', () => {
  it('keeps refused text until it is cleared', () => {
    const key = documentKey('chapter', 'held');
    expect(heldConflict(key)).toBe(null);
    holdConflict(key, 'My version.');
    expect(heldConflict(key)).toBe('My version.');
    clearConflict(key);
    expect(heldConflict(key)).toBe(null);
  });

  it('does not throw when storage is blocked', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new Error('blocked');
      },
      setItem: () => {
        throw new Error('blocked');
      },
      removeItem: () => {
        throw new Error('blocked');
      },
    });
    const key = documentKey('chapter', 'blocked');
    expect(() => holdConflict(key, 'x')).not.toThrow();
    expect(heldConflict(key)).toBe(null);
    expect(() => clearConflict(key)).not.toThrow();
  });
});
