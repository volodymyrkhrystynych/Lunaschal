// @vitest-environment jsdom
import { describe, it, expect, beforeEach } from 'vitest';
import { getStoredFicTarget, setStoredFicTarget } from './fanficPersistence';

beforeEach(() => {
  localStorage.clear();
});

describe('getStoredFicTarget', () => {
  it('returns null when nothing is stored', () => {
    expect(getStoredFicTarget()).toBeNull();
  });

  it('returns null for malformed JSON', () => {
    localStorage.setItem('lunaschal:openFic', 'not-json');
    expect(getStoredFicTarget()).toBeNull();
  });

  it('returns null when ficId is missing or the wrong type', () => {
    localStorage.setItem('lunaschal:openFic', JSON.stringify({}));
    expect(getStoredFicTarget()).toBeNull();

    localStorage.setItem('lunaschal:openFic', JSON.stringify({ ficId: 42 }));
    expect(getStoredFicTarget()).toBeNull();
  });

  it('reads back a previously stored target', () => {
    localStorage.setItem(
      'lunaschal:openFic',
      JSON.stringify({ ficId: 'fic1' })
    );
    expect(getStoredFicTarget()).toEqual({ ficId: 'fic1' });
  });
});

describe('setStoredFicTarget', () => {
  it('persists a target so it can be read back', () => {
    setStoredFicTarget({ ficId: 'fic1' });
    expect(getStoredFicTarget()).toEqual({ ficId: 'fic1' });
  });

  it('clears the stored target when set to null', () => {
    setStoredFicTarget({ ficId: 'fic1' });
    setStoredFicTarget(null);
    expect(getStoredFicTarget()).toBeNull();
  });
});
