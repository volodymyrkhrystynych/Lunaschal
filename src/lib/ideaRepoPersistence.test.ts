// @vitest-environment jsdom
import { describe, it, expect, beforeEach } from 'vitest';
import {
  ALL_REPOS,
  getStoredIdeaRepo,
  resolveIdeaRepo,
  setStoredIdeaRepo,
} from './ideaRepoPersistence';

describe('resolveIdeaRepo', () => {
  it('keeps a stored repo that still exists', () => {
    expect(resolveIdeaRepo('r2', ['r1', 'r2'])).toBe('r2');
  });

  it('falls back to all when the repo was removed', () => {
    // The rule that matters: filtering to a repo that is gone leaves an empty
    // list, and an Ideas tab that looks empty is indistinguishable from one
    // with no ideas in it.
    expect(resolveIdeaRepo('gone', ['r1', 'r2'])).toBe(ALL_REPOS);
  });

  it('falls back to all when nothing is stored', () => {
    expect(resolveIdeaRepo(null, ['r1'])).toBe(ALL_REPOS);
    expect(resolveIdeaRepo('', ['r1'])).toBe(ALL_REPOS);
  });

  it('passes the all sentinel through', () => {
    expect(resolveIdeaRepo(ALL_REPOS, ['r1'])).toBe(ALL_REPOS);
  });

  it('falls back to all when there are no repos at all', () => {
    expect(resolveIdeaRepo('r1', [])).toBe(ALL_REPOS);
  });
});

describe('storage', () => {
  beforeEach(() => localStorage.clear());

  it('round-trips a repo id', () => {
    setStoredIdeaRepo('r1');
    expect(getStoredIdeaRepo()).toBe('r1');
  });

  it('clears on null, so "all" is stored as absence rather than a value', () => {
    setStoredIdeaRepo('r1');
    setStoredIdeaRepo(null);
    expect(getStoredIdeaRepo()).toBeNull();
    expect(localStorage.getItem('lunaschal:ideaRepo')).toBeNull();
  });

  it('survives storage being unavailable', () => {
    // Private mode, or site data blocked. A forgotten filter is a small loss;
    // a crash on mount is not.
    const getItem = Storage.prototype.getItem;
    const setItem = Storage.prototype.setItem;
    Storage.prototype.getItem = () => {
      throw new Error('denied');
    };
    Storage.prototype.setItem = () => {
      throw new Error('denied');
    };
    try {
      expect(getStoredIdeaRepo()).toBeNull();
      expect(() => setStoredIdeaRepo('r1')).not.toThrow();
    } finally {
      Storage.prototype.getItem = getItem;
      Storage.prototype.setItem = setItem;
    }
  });
});
