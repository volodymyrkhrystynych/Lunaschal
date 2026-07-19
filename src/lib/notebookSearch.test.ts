import { describe, it, expect } from 'vitest';
import { matchesQuery } from './notebookSearch';

describe('matchesQuery', () => {
  it('matches case-insensitively', () => {
    expect(matchesQuery('Project Ideas.md', 'project')).toBe(true);
    expect(matchesQuery('project ideas.md', 'IDEAS')).toBe(true);
  });

  it('matches on a substring anywhere in the name', () => {
    expect(matchesQuery('daily-notes.md', 'notes')).toBe(true);
  });

  it('returns false when the name does not contain the query', () => {
    expect(matchesQuery('journal.md', 'recipe')).toBe(false);
  });

  it('treats an empty or whitespace-only query as matching everything', () => {
    expect(matchesQuery('anything.md', '')).toBe(true);
    expect(matchesQuery('anything.md', '   ')).toBe(true);
  });
});
