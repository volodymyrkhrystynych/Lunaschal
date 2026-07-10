import { describe, it, expect } from 'vitest';
import { shiftDateISO, isFutureDate, todayISO } from './newspapers';

describe('todayISO', () => {
  it('uses the local calendar date, not the UTC one', () => {
    // 11pm local time can already be tomorrow in UTC for timezones behind
    // UTC — todayISO must still report the viewer's local "today" so it
    // stays in sync with the backend's `date.today()` (also local).
    const localLateEvening = new Date(2026, 6, 9, 23, 0, 0); // Jul 9, 11pm, local time
    expect(todayISO(localLateEvening)).toBe('2026-07-09');
  });
});

describe('shiftDateISO', () => {
  it('moves forward and backward within a month', () => {
    expect(shiftDateISO('2026-07-10', 1)).toBe('2026-07-11');
    expect(shiftDateISO('2026-07-10', -1)).toBe('2026-07-09');
  });

  it('crosses a month boundary', () => {
    expect(shiftDateISO('2026-07-31', 1)).toBe('2026-08-01');
    expect(shiftDateISO('2026-08-01', -1)).toBe('2026-07-31');
  });

  it('crosses a year boundary', () => {
    expect(shiftDateISO('2026-12-31', 1)).toBe('2027-01-01');
    expect(shiftDateISO('2027-01-01', -1)).toBe('2026-12-31');
  });
});

describe('isFutureDate', () => {
  const now = new Date('2026-07-10T12:00:00Z');

  it('is false for today', () => {
    expect(isFutureDate('2026-07-10', now)).toBe(false);
  });

  it('is false for yesterday', () => {
    expect(isFutureDate('2026-07-09', now)).toBe(false);
  });

  it('is true for tomorrow', () => {
    expect(isFutureDate('2026-07-11', now)).toBe(true);
  });
});
