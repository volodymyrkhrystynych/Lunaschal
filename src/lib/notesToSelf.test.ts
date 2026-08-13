import { describe, it, expect } from 'vitest';
import { formatNoteCreatedAt } from './notesToSelf';

describe('formatNoteCreatedAt', () => {
  it('formats a date within the current year without the year', () => {
    const now = new Date('2026-08-12T00:00:00');
    expect(formatNoteCreatedAt('2026-07-14T14:20:00', now)).toBe(
      'Jul 14, 2:20 PM'
    );
  });

  it('appends the year for a date outside the current year', () => {
    const now = new Date('2026-08-12T00:00:00');
    expect(formatNoteCreatedAt('2025-07-14T14:20:00', now)).toBe(
      'Jul 14, 2025, 2:20 PM'
    );
  });

  it('returns an empty string for an unparseable date', () => {
    expect(formatNoteCreatedAt('not a date')).toBe('');
  });
});
