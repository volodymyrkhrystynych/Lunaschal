import { describe, it, expect } from 'vitest';
import {
  displayTitle,
  filterIdeas,
  parseTags,
  statusLabel,
  tagCounts,
} from './ideas';
import type { IdeaSummary } from '../hooks/api';

function idea(over: Partial<IdeaSummary> = {}): IdeaSummary {
  return {
    id: '1',
    title: 'Habit tracking',
    status: 'new',
    tags: null,
    sketchCount: 0,
    createdAt: '2026-08-01T00:00:00+00:00',
    updatedAt: '2026-08-01T00:00:00+00:00',
    ...over,
  };
}

describe('parseTags', () => {
  it('reads a JSON array column', () => {
    expect(parseTags('["ui", "backend"]')).toEqual(['ui', 'backend']);
  });

  it('treats null, empty and malformed as no tags', () => {
    expect(parseTags(null)).toEqual([]);
    expect(parseTags('')).toEqual([]);
    expect(parseTags('not json')).toEqual([]);
    // A non-array JSON value parses fine but is not a tag list.
    expect(parseTags('{"a":1}')).toEqual([]);
  });

  it('drops non-string and empty entries', () => {
    expect(parseTags('["ui", 3, null, "", "api"]')).toEqual(['ui', 'api']);
  });
});

describe('displayTitle', () => {
  it('prefers the real title', () => {
    expect(displayTitle({ title: 'Habit tracking', rawContent: 'other' })).toBe(
      'Habit tracking'
    );
  });

  it('falls back to the first line of a dictated idea', () => {
    expect(
      displayTitle({ title: '', rawContent: 'a habit grid\nsecond line' })
    ).toBe('a habit grid');
  });

  it('clips a long first line on a word boundary', () => {
    const raw =
      'the quick brown fox jumps over the lazy dog and keeps on going forever';
    const out = displayTitle({ title: '', rawContent: raw }, 20);
    expect(out.endsWith('…')).toBe(true);
    expect(out.length).toBeLessThanOrEqual(21);
    // Clipped between words, not mid-word.
    expect(raw.startsWith(out.slice(0, -1))).toBe(true);
    expect(out.slice(0, -1).endsWith(' ')).toBe(false);
  });

  it('clips mid-word only when the first word is itself too long', () => {
    const out = displayTitle(
      { title: '', rawContent: 'supercalifragilistic' },
      10
    );
    expect(out).toBe('supercalif…');
  });

  it('names an idea with nothing in it at all', () => {
    expect(displayTitle({ title: '  ', rawContent: '  ' })).toBe(
      'Untitled idea'
    );
    expect(displayTitle({ title: '' })).toBe('Untitled idea');
  });
});

describe('filterIdeas', () => {
  const ideas = [
    idea({ id: '1', title: 'Habit tracking', status: 'new', tags: '["ui"]' }),
    idea({
      id: '2',
      title: 'Global search',
      status: 'ready',
      tags: '["backend","ui"]',
    }),
    idea({ id: '3', title: 'Encrypted backups', status: 'ready', tags: null }),
  ];

  it('returns everything with an empty filter', () => {
    expect(filterIdeas(ideas, {})).toHaveLength(3);
  });

  it('filters by status, treating "all" as no filter', () => {
    expect(filterIdeas(ideas, { status: 'ready' }).map(i => i.id)).toEqual([
      '2',
      '3',
    ]);
    expect(filterIdeas(ideas, { status: 'all' })).toHaveLength(3);
  });

  it('filters by tag', () => {
    expect(filterIdeas(ideas, { tag: 'ui' }).map(i => i.id)).toEqual([
      '1',
      '2',
    ]);
  });

  it('matches the query case-insensitively', () => {
    expect(filterIdeas(ideas, { query: '  SEARCH ' }).map(i => i.id)).toEqual([
      '2',
    ]);
  });

  it('combines filters', () => {
    expect(
      filterIdeas(ideas, { status: 'ready', tag: 'ui' }).map(i => i.id)
    ).toEqual(['2']);
  });
});

describe('tagCounts', () => {
  it('counts by frequency then name', () => {
    const ideas = [
      idea({ id: '1', tags: '["ui","backend"]' }),
      idea({ id: '2', tags: '["ui"]' }),
      idea({ id: '3', tags: '["api"]' }),
    ];
    expect(tagCounts(ideas)).toEqual([
      { name: 'ui', count: 2 },
      { name: 'api', count: 1 },
      { name: 'backend', count: 1 },
    ]);
  });

  it('is empty when nothing is tagged', () => {
    expect(tagCounts([idea(), idea({ id: '2' })])).toEqual([]);
  });
});

describe('statusLabel', () => {
  it('labels every status', () => {
    expect(statusLabel('new')).toBe('New');
    expect(statusLabel('shipped')).toBe('Shipped');
  });
});
