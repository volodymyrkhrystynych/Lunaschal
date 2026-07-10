import { describe, it, expect } from 'vitest';
import { adjacentChapter, chapterIdsUpTo, detectFicSite, formatRating, groupChaptersByCategory, orderChapters } from './fanfic';
import type { FicChapterSummary } from '@/hooks/api';

describe('detectFicSite', () => {
  it('detects all three forums', () => {
    expect(detectFicSite('https://forums.spacebattles.com/threads/x.1/')).toBe('spacebattles');
    expect(detectFicSite('https://forums.sufficientvelocity.com/threads/x.1/')).toBe('sufficientvelocity');
    expect(detectFicSite('https://forum.questionablequesting.com/threads/x.1/')).toBe('questionablequesting');
  });

  it('handles www prefixes', () => {
    expect(detectFicSite('https://www.forums.spacebattles.com/threads/x.1/')).toBe('spacebattles');
  });

  it('returns null for other URLs and garbage', () => {
    expect(detectFicSite('https://archiveofourown.org/works/1')).toBeNull();
    expect(detectFicSite('not a url')).toBeNull();
    expect(detectFicSite('')).toBeNull();
  });
});

function ch(id: string, category: string, position: number): FicChapterSummary {
  return { id, ficId: 'f', position, title: id, category, wordCount: 0, postedAt: null, isRead: false };
}

const CHAPTERS = [
  ch('side1', 'Sidestory', 1),
  ch('main2', 'Threadmarks', 2),
  ch('main1', 'Threadmarks', 1),
  ch('apoc1', 'Apocrypha', 1),
  ch('main3', 'Threadmarks', 3),
];

describe('orderChapters', () => {
  it('puts the main category first, then others alphabetically, by position', () => {
    expect(orderChapters(CHAPTERS).map((c) => c.id)).toEqual(
      ['main1', 'main2', 'main3', 'apoc1', 'side1']);
  });

  it('is case-insensitive about the main category and handles file fics', () => {
    const file = [ch('b', 'chapters', 2), ch('a', 'chapters', 1)];
    expect(orderChapters(file).map((c) => c.id)).toEqual(['a', 'b']);
  });

  it('handles empty input', () => {
    expect(orderChapters([])).toEqual([]);
  });
});

describe('adjacentChapter', () => {
  it('moves forward and backward across category boundaries', () => {
    expect(adjacentChapter(CHAPTERS, 'main1', 1)?.id).toBe('main2');
    expect(adjacentChapter(CHAPTERS, 'main2', -1)?.id).toBe('main1');
    expect(adjacentChapter(CHAPTERS, 'main3', 1)?.id).toBe('apoc1');
  });

  it('returns null at the edges and for unknown ids', () => {
    expect(adjacentChapter(CHAPTERS, 'main1', -1)).toBeNull();
    expect(adjacentChapter(CHAPTERS, 'side1', 1)).toBeNull();
    expect(adjacentChapter(CHAPTERS, 'nope', 1)).toBeNull();
    expect(adjacentChapter([], 'main1', 1)).toBeNull();
  });
});

describe('groupChaptersByCategory', () => {
  it('groups in display order', () => {
    const groups = groupChaptersByCategory(CHAPTERS);
    expect(groups.map(([name, chs]) => [name, chs.length])).toEqual([
      ['Threadmarks', 3],
      ['Apocrypha', 1],
      ['Sidestory', 1],
    ]);
  });
});

describe('chapterIdsUpTo', () => {
  it('returns ids in reading order up to and including the target', () => {
    expect(chapterIdsUpTo(CHAPTERS, 'main2')).toEqual(['main1', 'main2']);
    expect(chapterIdsUpTo(CHAPTERS, 'main1')).toEqual(['main1']);
  });

  it('crosses category boundaries in display order', () => {
    expect(chapterIdsUpTo(CHAPTERS, 'apoc1')).toEqual(['main1', 'main2', 'main3', 'apoc1']);
    expect(chapterIdsUpTo(CHAPTERS, 'side1')).toEqual(
      ['main1', 'main2', 'main3', 'apoc1', 'side1']);
  });

  it('returns [] for unknown ids and empty input', () => {
    expect(chapterIdsUpTo(CHAPTERS, 'nope')).toEqual([]);
    expect(chapterIdsUpTo([], 'main1')).toEqual([]);
  });
});

describe('formatRating', () => {
  it('renders 1-5 stars', () => {
    expect(formatRating(1)).toBe('★☆☆☆☆');
    expect(formatRating(3)).toBe('★★★☆☆');
    expect(formatRating(5)).toBe('★★★★★');
  });

  it('returns null for unrated or out-of-range values', () => {
    expect(formatRating(null)).toBeNull();
    expect(formatRating(undefined)).toBeNull();
    expect(formatRating(0)).toBeNull();
    expect(formatRating(6)).toBeNull();
  });
});
