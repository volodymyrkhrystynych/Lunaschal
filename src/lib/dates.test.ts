import { describe, it, expect } from 'vitest';
import { localDayKey } from './dates';

describe('localDayKey', () => {
  it('keeps a mid-afternoon timestamp on its calendar day', () => {
    expect(localDayKey(new Date('2026-08-12T15:30:00'))).toBe('2026-08-12');
  });

  it('rolls a timestamp just after midnight back to the previous day', () => {
    expect(localDayKey(new Date('2026-08-12T02:30:00'))).toBe('2026-08-11');
  });

  it('rolls a timestamp right at 3:59am back to the previous day', () => {
    expect(localDayKey(new Date('2026-08-12T03:59:59'))).toBe('2026-08-11');
  });

  it('counts 4:00am exactly as the start of the new day', () => {
    expect(localDayKey(new Date('2026-08-12T04:00:00'))).toBe('2026-08-12');
  });

  it('defaults to the current time when called with no argument', () => {
    expect(() => localDayKey()).not.toThrow();
  });
});
