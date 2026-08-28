import { describe, it, expect } from 'vitest';
import {
  decisionAnswer,
  decisionChoices,
  displayTitle,
  filterIdeas,
  implementationLabel,
  needsDecisions,
  parseTags,
  resolveImplementation,
  selectedChoice,
  statusLabel,
  tagCounts,
  OTHER_CHOICE,
} from './ideas';
import type { IdeaSummary } from '../hooks/api';

function idea(over: Partial<IdeaSummary> = {}): IdeaSummary {
  return {
    id: '1',
    title: 'Habit tracking',
    status: 'new',
    tags: null,
    sketchCount: 0,
    openQuestionCount: 0,
    articleCount: 0,
    hasPlan: false,
    verdict: null,
    confidence: null,
    effort: null,
    onRoadmap: false,
    assessmentStale: false,
    userVerdict: null,
    researchState: 'idle',
    repoId: null,
    createdAt: '2026-08-01T00:00:00+00:00',
    updatedAt: '2026-08-01T00:00:00+00:00',
    ...over,
  };
}

describe('resolveImplementation', () => {
  it('reports nothing before an assessment exists', () => {
    const impl = resolveImplementation(idea());
    expect(impl).toEqual({
      verdict: null,
      source: null,
      confidence: null,
      stale: false,
    });
    expect(implementationLabel(impl)).toBe('Not assessed');
  });

  it('uses the agent verdict with its confidence', () => {
    const impl = resolveImplementation(
      idea({ verdict: 'partial', confidence: 0.62 })
    );
    expect(impl.source).toBe('agent');
    expect(implementationLabel(impl)).toBe('Partly built 62%');
  });

  it("lets the user's verdict beat the agent's, and drops the confidence", () => {
    const impl = resolveImplementation(
      idea({ verdict: 'no', confidence: 0.9, userVerdict: 'yes' })
    );
    expect(impl).toEqual({
      verdict: 'yes',
      source: 'user',
      confidence: null,
      stale: false,
    });
    expect(implementationLabel(impl)).toBe('Already built (you)');
  });

  it('marks an agent verdict stale once the repo has moved on', () => {
    const impl = resolveImplementation(
      idea({ verdict: 'yes', confidence: 0.8, assessmentStale: true })
    );
    expect(impl.stale).toBe(true);
    expect(implementationLabel(impl)).toContain('stale');
  });

  it('never calls a user verdict stale — a human decision does not expire', () => {
    const impl = resolveImplementation(
      idea({ verdict: 'yes', userVerdict: 'no', assessmentStale: true })
    );
    expect(impl.stale).toBe(false);
  });
});

describe('needsDecisions', () => {
  it('is true only when a question is unanswered', () => {
    expect(needsDecisions(idea())).toBe(false);
    expect(needsDecisions(idea({ openQuestionCount: 2 }))).toBe(true);
  });
});

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

  describe('by repository', () => {
    const mixed = [
      idea({ id: 'a', title: 'One', repoId: 'r1' }),
      idea({ id: 'b', title: 'Two', repoId: 'r2' }),
      // An idea belonging to no repo: a plain product thought, captured
      // before any repository was registered.
      idea({ id: 'c', title: 'Three', repoId: null }),
    ];

    it('shows only that repo when one is chosen', () => {
      expect(filterIdeas(mixed, { repoId: 'r1' }).map(i => i.id)).toEqual([
        'a',
      ]);
    });

    it('shows everything, including repo-less ideas, for "all"', () => {
      expect(filterIdeas(mixed, { repoId: 'all' })).toHaveLength(3);
      expect(filterIdeas(mixed, {})).toHaveLength(3);
    });

    it('combines with the other filters', () => {
      const rows = [
        idea({ id: 'a', repoId: 'r1', status: 'ready' }),
        idea({ id: 'b', repoId: 'r1', status: 'new' }),
        idea({ id: 'c', repoId: 'r2', status: 'ready' }),
      ];
      expect(
        filterIdeas(rows, { repoId: 'r1', status: 'ready' }).map(i => i.id)
      ).toEqual(['a']);
    });
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

describe('decisionChoices', () => {
  it("keeps the agent's options in order and appends the write-in", () => {
    const rows = decisionChoices(['Paper pages', 'A new table']);
    expect(rows.map(r => r.value)).toEqual([
      'Paper pages',
      'A new table',
      OTHER_CHOICE,
    ]);
    expect(rows[2]!.isOther).toBe(true);
  });

  it('always offers the write-in, even with nothing proposed', () => {
    // A decision the agent could not enumerate is still a decision; degrading
    // to a bare text field is exactly what this used to be.
    const rows = decisionChoices([]);
    expect(rows).toHaveLength(1);
    expect(rows[0]!.isOther).toBe(true);
  });

  it('drops blanks and case-insensitive duplicates', () => {
    expect(
      decisionChoices(['Paper', '  ', 'paper', ' Paper ']).map(r => r.value)
    ).toEqual(['Paper', OTHER_CHOICE]);
  });
});

describe('selectedChoice', () => {
  it('has nothing selected before a decision is made', () => {
    expect(selectedChoice(['Paper', 'Table'], null)).toEqual({
      value: '',
      note: '',
    });
  });

  it('selects the option that was chosen', () => {
    expect(selectedChoice(['Paper', 'Table'], 'Table')).toEqual({
      value: 'Table',
      note: '',
    });
  });

  it('puts a hand-written answer back in the write-in box', () => {
    // Reopening a settled decision should show what was decided, not a blank.
    expect(
      selectedChoice(['Paper', 'Table'], 'Both, keyed by page id')
    ).toEqual({ value: OTHER_CHOICE, note: 'Both, keyed by page id' });
  });
});

describe('decisionAnswer', () => {
  it('is the option itself when one is picked', () => {
    expect(decisionAnswer('Paper', 'ignored')).toBe('Paper');
  });

  it('is the trimmed note when the write-in row is picked', () => {
    expect(decisionAnswer(OTHER_CHOICE, '  Both  ')).toBe('Both');
  });

  it('is empty — so nothing can be submitted — until something is chosen', () => {
    expect(decisionAnswer('', 'typed but unselected')).toBe('');
    expect(decisionAnswer(OTHER_CHOICE, '   ')).toBe('');
  });
});
