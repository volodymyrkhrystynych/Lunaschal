import { describe, it, expect } from 'vitest';
import { shiftDateISO, isFutureDate } from './newspapers';

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
