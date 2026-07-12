// @vitest-environment jsdom
import { describe, it, expect, beforeEach } from 'vitest';
import {
  FONT_SIZE_DEFAULT, FONT_SIZE_MAX, FONT_SIZE_MIN,
  CHAPTER_FONT_SIZE_DEFAULT, CHAPTER_FONT_SIZE_MAX, CHAPTER_FONT_SIZE_MIN,
  applyFontSize, getStoredFontSize, setStoredFontSize,
  getStoredChapterFontSize, setStoredChapterFontSize,
} from './fontSize';

beforeEach(() => {
  localStorage.clear();
  document.documentElement.style.fontSize = '';
});

describe('getStoredFontSize', () => {
  it('falls back to the default when nothing is stored', () => {
    expect(getStoredFontSize()).toBe(FONT_SIZE_DEFAULT);
  });

  it('falls back to the default for garbage values', () => {
    localStorage.setItem('lunaschal:fontSize', 'not-a-number');
    expect(getStoredFontSize()).toBe(FONT_SIZE_DEFAULT);
  });

  it('reads back a previously stored size', () => {
    localStorage.setItem('lunaschal:fontSize', '18');
    expect(getStoredFontSize()).toBe(18);
  });

  it('clamps out-of-range stored values', () => {
    localStorage.setItem('lunaschal:fontSize', '999');
    expect(getStoredFontSize()).toBe(FONT_SIZE_MAX);
    localStorage.setItem('lunaschal:fontSize', '0');
    expect(getStoredFontSize()).toBe(FONT_SIZE_MIN);
  });
});

describe('setStoredFontSize', () => {
  it('persists and applies the clamped size, returning it', () => {
    const result = setStoredFontSize(20);
    expect(result).toBe(20);
    expect(getStoredFontSize()).toBe(20);
    expect(document.documentElement.style.fontSize).toBe('20px');
  });

  it('clamps values outside the allowed range', () => {
    expect(setStoredFontSize(1000)).toBe(FONT_SIZE_MAX);
    expect(setStoredFontSize(-5)).toBe(FONT_SIZE_MIN);
  });
});

describe('chapter font size', () => {
  it('falls back to the default when nothing is stored', () => {
    expect(getStoredChapterFontSize()).toBe(CHAPTER_FONT_SIZE_DEFAULT);
  });

  it('falls back to the default for garbage values', () => {
    localStorage.setItem('lunaschal:chapterFontSize', 'not-a-number');
    expect(getStoredChapterFontSize()).toBe(CHAPTER_FONT_SIZE_DEFAULT);
  });

  it('persists the clamped size and reads it back', () => {
    expect(setStoredChapterFontSize(22)).toBe(22);
    expect(getStoredChapterFontSize()).toBe(22);
  });

  it('clamps values outside the allowed range', () => {
    expect(setStoredChapterFontSize(1000)).toBe(CHAPTER_FONT_SIZE_MAX);
    expect(setStoredChapterFontSize(-5)).toBe(CHAPTER_FONT_SIZE_MIN);
  });

  it('is stored independently of the base UI font size', () => {
    setStoredFontSize(14);
    setStoredChapterFontSize(24);
    expect(getStoredFontSize()).toBe(14);
    expect(getStoredChapterFontSize()).toBe(24);
  });
});

describe('applyFontSize', () => {
  it('sets the root element font size without touching storage', () => {
    applyFontSize(15);
    expect(document.documentElement.style.fontSize).toBe('15px');
    expect(localStorage.getItem('lunaschal:fontSize')).toBeNull();
  });
});
