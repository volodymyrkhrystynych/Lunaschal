import { describe, it, expect } from 'vitest';
import {
  resolveInitialChapter,
  scrollFraction,
  scrollTopForFraction,
} from './fanficBookmarks';
import type { FicChapterSummary } from '@/hooks/api';

describe('scrollFraction', () => {
  it('computes a clamped 0-1 fraction of the scrollable range', () => {
    expect(scrollFraction(0, 1000, 500)).toBe(0);
    expect(scrollFraction(250, 1000, 500)).toBe(0.5);
    expect(scrollFraction(500, 1000, 500)).toBe(1);
  });

  it('clamps out-of-range values', () => {
    expect(scrollFraction(-50, 1000, 500)).toBe(0);
    expect(scrollFraction(9999, 1000, 500)).toBe(1);
  });

  it('reads as 0 when content is shorter than the viewport', () => {
    expect(scrollFraction(0, 300, 500)).toBe(0);
  });
});

describe('scrollTopForFraction', () => {
  it('is the inverse of scrollFraction', () => {
    expect(scrollTopForFraction(0.5, 1000, 500)).toBe(250);
    expect(scrollTopForFraction(0, 1000, 500)).toBe(0);
    expect(scrollTopForFraction(1, 1000, 500)).toBe(500);
  });

  it('clamps fractions outside 0-1', () => {
    expect(scrollTopForFraction(-1, 1000, 500)).toBe(0);
    expect(scrollTopForFraction(2, 1000, 500)).toBe(500);
  });

  it('returns 0 when there is nothing to scroll', () => {
    expect(scrollTopForFraction(0.5, 300, 500)).toBe(0);
  });
});

function ch(id: string): FicChapterSummary {
  return {
    id,
    ficId: 'f',
    position: 1,
    title: id,
    category: 'Threadmarks',
    wordCount: 0,
    postedAt: null,
    isRead: false,
  };
}

const CHAPTERS = [ch('one'), ch('two'), ch('three')];

describe('resolveInitialChapter', () => {
  it('returns null for an empty fic', () => {
    expect(resolveInitialChapter([], {})).toBeNull();
  });

  it('falls back to the first chapter when nothing else is set', () => {
    expect(resolveInitialChapter(CHAPTERS, {})?.id).toBe('one');
  });

  it('prefers the last-read chapter over the first', () => {
    expect(
      resolveInitialChapter(CHAPTERS, { lastReadChapterId: 'two' })?.id
    ).toBe('two');
  });

  it('prefers the continue bookmark over last-read', () => {
    expect(
      resolveInitialChapter(CHAPTERS, {
        lastReadChapterId: 'two',
        continueChapterId: 'three',
      })?.id
    ).toBe('three');
  });

  it('prefers an explicit target over everything', () => {
    expect(
      resolveInitialChapter(CHAPTERS, {
        lastReadChapterId: 'two',
        continueChapterId: 'three',
        targetChapterId: 'one',
      })?.id
    ).toBe('one');
  });

  it('skips ids pointing at chapters that no longer exist', () => {
    expect(
      resolveInitialChapter(CHAPTERS, {
        targetChapterId: 'deleted',
        continueChapterId: 'also-deleted',
        lastReadChapterId: 'two',
      })?.id
    ).toBe('two');
  });
});
