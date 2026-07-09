import { describe, it, expect } from 'vitest';
import { partitionTodos, formatCompletedAt } from './todos';

const todo = (id: string, done: boolean, completedAt: string | null) => ({
  id,
  done,
  completedAt,
});

describe('partitionTodos', () => {
  it('separates active from completed', () => {
    const { active, completed } = partitionTodos([
      todo('a', false, null),
      todo('b', true, '2026-07-01T10:00:00+00:00'),
      todo('c', false, null),
    ]);
    expect(active.map((t) => t.id)).toEqual(['a', 'c']);
    expect(completed.map((t) => t.id)).toEqual(['b']);
  });

  it('orders completed most-recent first', () => {
    const { completed } = partitionTodos([
      todo('old', true, '2026-06-01T10:00:00+00:00'),
      todo('new', true, '2026-07-05T10:00:00+00:00'),
      todo('mid', true, '2026-06-20T10:00:00+00:00'),
    ]);
    expect(completed.map((t) => t.id)).toEqual(['new', 'mid', 'old']);
  });

  it('puts legacy completed todos without a timestamp last', () => {
    const { completed } = partitionTodos([
      todo('legacy', true, null),
      todo('dated', true, '2026-07-05T10:00:00+00:00'),
    ]);
    expect(completed.map((t) => t.id)).toEqual(['dated', 'legacy']);
  });

  it('handles an empty list', () => {
    expect(partitionTodos([])).toEqual({ active: [], completed: [] });
  });
});

describe('formatCompletedAt', () => {
  const now = new Date('2026-07-08T12:00:00Z');

  it('returns empty string for null or invalid input', () => {
    expect(formatCompletedAt(null, now)).toBe('');
    expect(formatCompletedAt('not-a-date', now)).toBe('');
  });

  it('omits the year for dates in the current year', () => {
    const s = formatCompletedAt('2026-07-05T10:00:00+00:00', now);
    expect(s).toMatch(/^Jul \d{1,2}, \d{1,2}:\d{2}\s?[AP]M$/);
    expect(s).not.toContain('2026');
  });

  it('includes the year for dates in a previous year', () => {
    const s = formatCompletedAt('2025-12-31T10:00:00+00:00', now);
    expect(s).toContain('2025');
  });
});
