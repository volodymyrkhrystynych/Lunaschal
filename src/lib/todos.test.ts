import { describe, it, expect } from 'vitest';
import {
  partitionTodos,
  formatCompletedAt,
  groupTodosByList,
  formatDue,
  repeatLabel,
  dueInputToUnix,
  dueIsoToInput,
} from './todos';

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
    expect(active.map(t => t.id)).toEqual(['a', 'c']);
    expect(completed.map(t => t.id)).toEqual(['b']);
  });

  it('orders completed most-recent first', () => {
    const { completed } = partitionTodos([
      todo('old', true, '2026-06-01T10:00:00+00:00'),
      todo('new', true, '2026-07-05T10:00:00+00:00'),
      todo('mid', true, '2026-06-20T10:00:00+00:00'),
    ]);
    expect(completed.map(t => t.id)).toEqual(['new', 'mid', 'old']);
  });

  it('puts legacy completed todos without a timestamp last', () => {
    const { completed } = partitionTodos([
      todo('legacy', true, null),
      todo('dated', true, '2026-07-05T10:00:00+00:00'),
    ]);
    expect(completed.map(t => t.id)).toEqual(['dated', 'legacy']);
  });

  it('handles an empty list', () => {
    expect(partitionTodos([])).toEqual({ active: [], completed: [] });
  });
});

describe('groupTodosByList', () => {
  it('buckets todos into the three lists', () => {
    const buckets = groupTodosByList([
      { id: 'a', list: 'todo' },
      { id: 'b', list: 'chores' },
      { id: 'c', list: 'archive' },
      { id: 'd', list: 'todo' },
    ]);
    expect(buckets.todo.map(t => t.id)).toEqual(['a', 'd']);
    expect(buckets.chores.map(t => t.id)).toEqual(['b']);
    expect(buckets.archive.map(t => t.id)).toEqual(['c']);
  });

  it('always returns all three buckets', () => {
    expect(groupTodosByList([])).toEqual({ todo: [], chores: [], archive: [] });
  });

  it('sends unknown list values to todo', () => {
    const buckets = groupTodosByList([{ id: 'x', list: 'someday' }]);
    expect(buckets.todo.map(t => t.id)).toEqual(['x']);
  });
});

describe('formatDue', () => {
  const now = new Date(2026, 6, 8, 12, 0, 0); // local Jul 8, 2026

  it('returns null for null or invalid input', () => {
    expect(formatDue(null, now)).toBeNull();
    expect(formatDue('not-a-date', now)).toBeNull();
  });

  it('labels current-year dates without the year', () => {
    const r = formatDue(new Date(2026, 6, 24, 12).toISOString(), now)!;
    expect(r.label).toMatch(/^Jul \d{1,2}$/);
    expect(r.overdue).toBe(false);
  });

  it('includes the year for other years', () => {
    const r = formatDue(new Date(2025, 11, 31, 12).toISOString(), now)!;
    expect(r.label).toContain('2025');
    expect(r.overdue).toBe(true);
  });

  it('marks yesterday overdue but not today or tomorrow', () => {
    expect(
      formatDue(new Date(2026, 6, 7, 23).toISOString(), now)!.overdue
    ).toBe(true);
    expect(formatDue(new Date(2026, 6, 8, 0).toISOString(), now)!.overdue).toBe(
      false
    );
    expect(formatDue(new Date(2026, 6, 9, 1).toISOString(), now)!.overdue).toBe(
      false
    );
  });
});

describe('repeatLabel', () => {
  it('drops the number for interval 1', () => {
    expect(repeatLabel(1, 'day')).toBe('every day');
  });

  it('pluralizes larger intervals', () => {
    expect(repeatLabel(2, 'week')).toBe('every 2 weeks');
    expect(repeatLabel(3, 'month')).toBe('every 3 months');
  });

  it('returns empty for missing values', () => {
    expect(repeatLabel(null, null)).toBe('');
    expect(repeatLabel(2, null)).toBe('');
    expect(repeatLabel(null, 'day')).toBe('');
  });
});

describe('due date input round-trip', () => {
  it('converts a date input value to unix and back preserving the date', () => {
    const unix = dueInputToUnix('2026-07-25')!;
    expect(unix).not.toBeNull();
    const iso = new Date(unix * 1000).toISOString();
    expect(dueIsoToInput(iso)).toBe('2026-07-25');
  });

  it('returns null/empty for blank or malformed input', () => {
    expect(dueInputToUnix('')).toBeNull();
    expect(dueInputToUnix('tomorrow')).toBeNull();
    expect(dueIsoToInput(null)).toBe('');
    expect(dueIsoToInput('junk')).toBe('');
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
