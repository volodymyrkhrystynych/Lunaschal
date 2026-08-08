// @vitest-environment jsdom
import { describe, it, expect, beforeEach } from 'vitest';
import { getStoredSpeechMode, setStoredSpeechMode } from './learningSpeechMode';

beforeEach(() => {
  localStorage.clear();
});

describe('getStoredSpeechMode', () => {
  it('defaults to off when nothing is stored', () => {
    expect(getStoredSpeechMode()).toBe(false);
  });

  it('reads back a previously stored value', () => {
    localStorage.setItem('lunaschal:learningSpeechMode', '1');
    expect(getStoredSpeechMode()).toBe(true);
  });

  it('treats anything other than "1" as off', () => {
    localStorage.setItem('lunaschal:learningSpeechMode', 'true');
    expect(getStoredSpeechMode()).toBe(false);
  });
});

describe('setStoredSpeechMode', () => {
  it('persists and reads back true', () => {
    expect(setStoredSpeechMode(true)).toBe(true);
    expect(getStoredSpeechMode()).toBe(true);
  });

  it('persists and reads back false', () => {
    setStoredSpeechMode(true);
    expect(setStoredSpeechMode(false)).toBe(false);
    expect(getStoredSpeechMode()).toBe(false);
  });
});
