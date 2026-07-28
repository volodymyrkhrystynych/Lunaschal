import { describe, expect, it } from 'vitest';

import { formatMessageTime } from './chatTime';

// Inputs are built from local components (matching todos.test.ts) so the
// expected rendering holds in any timezone the suite runs in.
describe('formatMessageTime', () => {
  it('renders a 12-hour clock time', () => {
    expect(formatMessageTime(new Date(2026, 6, 24, 14, 5).toISOString())).toBe(
      '2:05 PM'
    );
    expect(formatMessageTime(new Date(2026, 6, 24, 9, 30).toISOString())).toBe(
      '9:30 AM'
    );
  });

  it('pads minutes but not hours', () => {
    expect(formatMessageTime(new Date(2026, 6, 24, 8, 7).toISOString())).toBe(
      '8:07 AM'
    );
  });

  it('handles both ends of the chat day', () => {
    // Midnight and the 4am rollover, the two places a bare time is easiest
    // to get wrong.
    expect(formatMessageTime(new Date(2026, 6, 24, 0, 0).toISOString())).toBe(
      '12:00 AM'
    );
    expect(formatMessageTime(new Date(2026, 6, 24, 12, 0).toISOString())).toBe(
      '12:00 PM'
    );
    expect(formatMessageTime(new Date(2026, 6, 24, 3, 59).toISOString())).toBe(
      '3:59 AM'
    );
  });

  it('returns null for missing or unparseable timestamps', () => {
    expect(formatMessageTime(null)).toBeNull();
    expect(formatMessageTime(undefined)).toBeNull();
    expect(formatMessageTime('')).toBeNull();
    expect(formatMessageTime('not-a-date')).toBeNull();
  });
});
