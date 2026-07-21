import { describe, it, expect } from 'vitest';
import {
  partitionTodos,
  isFarOffPeriodic,
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

  it('sorts active todos with due dates first, soonest on top', () => {
    const withDue = (id: string, due: string | null) => ({
      id,
      done: false,
      completedAt: null,
      due,
    });
    const { active } = partitionTodos([
      withDue('later', '2026-08-01T12:00:00+00:00'),
      withDue('none1', null),
      withDue('soon', '2026-07-21T12:00:00+00:00'),
      withDue('none2', null),
    ]);
    expect(active.map(t => t.id)).toEqual(['soon', 'later', 'none1', 'none2']);
  });

  it('keeps creation order among due-less todos and after equal dues', () => {
    const withDue = (id: string, due: string | null) => ({
      id,
      done: false,
      completedAt: null,
      due,
    });
    const { active } = partitionTodos([
      withDue('a', '2026-07-21T12:00:00+00:00'),
      withDue('b', '2026-07-21T12:00:00+00:00'),
      withDue('c', null),
      withDue('d', null),
    ]);
    expect(active.map(t => t.id)).toEqual(['a', 'b', 'c', 'd']);
  });

  it('still sorts active todos that lack a due field entirely', () => {
    const { active } = partitionTodos([
      todo('x', false, null),
      todo('y', false, null),
    ]);
    expect(active.map(t => t.id)).toEqual(['x', 'y']);
  });
});

describe('isFarOffPeriodic', () => {
  const now = new Date(2026, 6, 8, 12, 0, 0); // local Jul 8, 2026
  const at = (y: number, m: number, d: number) =>
    new Date(y, m, d, 12).toISOString();
  const periodic = (
    due: string | null,
    repeatInterval: number | null,
    repeatUnit: string | null
  ) => ({ due, repeatInterval, repeatUnit });

  it('is false for non-repeating todos, even far-off ones', () => {
    expect(isFarOffPeriodic(periodic(at(2026, 6, 30), null, null), now)).toBe(
      false
    );
  });

  it('is false for a repeating todo with no due date', () => {
    expect(isFarOffPeriodic(periodic(null, 1, 'week'), now)).toBe(false);
  });

  it('hides a weekly chore due beyond its 1-day window', () => {
    // 1 week -> threshold ceil(0.7) = 1 day.
    expect(isFarOffPeriodic(periodic(at(2026, 6, 13), 1, 'week'), now)).toBe(
      true
    ); // +5 days
    expect(isFarOffPeriodic(periodic(at(2026, 6, 10), 1, 'week'), now)).toBe(
      true
    ); // +2 days
    expect(isFarOffPeriodic(periodic(at(2026, 6, 9), 1, 'week'), now)).toBe(
      false
    ); // +1 day (within window)
  });

  it('hides a monthly chore until within ~3 days of due', () => {
    // 1 month ~30 days -> threshold ceil(3) = 3 days.
    expect(isFarOffPeriodic(periodic(at(2026, 6, 13), 1, 'month'), now)).toBe(
      true
    ); // +5 days
    expect(isFarOffPeriodic(periodic(at(2026, 6, 10), 1, 'month'), now)).toBe(
      false
    ); // +2 days
  });

  it('always shows due-today and overdue periodic todos', () => {
    expect(isFarOffPeriodic(periodic(at(2026, 6, 8), 1, 'month'), now)).toBe(
      false
    ); // today
    expect(isFarOffPeriodic(periodic(at(2026, 6, 1), 1, 'month'), now)).toBe(
      false
    ); // overdue
  });

  it('removes hidden periodic todos from the active list', () => {
    const base = { done: false, completedAt: null };
    const { active } = partitionTodos(
      [
        { id: 'faroff', ...base, ...periodic(at(2026, 6, 30), 1, 'month') },
        { id: 'soon', ...base, ...periodic(at(2026, 6, 9), 1, 'week') },
        { id: 'oneoff', ...base, ...periodic(at(2026, 7, 30), null, null) },
      ],
      now
    );
    expect(active.map(t => t.id)).toEqual(['soon', 'oneoff']);
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
