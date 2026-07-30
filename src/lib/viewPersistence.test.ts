// @vitest-environment jsdom
import { describe, it, expect, beforeEach } from 'vitest';
import { getStoredView, setStoredView, VIEWS } from './viewPersistence';

beforeEach(() => {
  localStorage.clear();
});

describe('getStoredView', () => {
  it('returns null when nothing is stored', () => {
    expect(getStoredView()).toBeNull();
  });

  it('returns null for a garbage/stale value', () => {
    localStorage.setItem('lunaschal:currentView', 'not-a-view');
    expect(getStoredView()).toBeNull();
  });

  it('reads back a previously stored view', () => {
    localStorage.setItem('lunaschal:currentView', 'fanfic');
    expect(getStoredView()).toBe('fanfic');
  });

  it('accepts every known view', () => {
    for (const view of VIEWS) {
      localStorage.setItem('lunaschal:currentView', view);
      expect(getStoredView()).toBe(view);
    }
  });
});

describe('setStoredView', () => {
  it('persists the view so it can be read back', () => {
    setStoredView('learning');
    expect(getStoredView()).toBe('learning');
  });
});
