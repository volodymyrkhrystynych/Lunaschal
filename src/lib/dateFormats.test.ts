import { describe, it, expect } from 'vitest';
import {
  dateTimeFormat,
  dayFormat,
  formatDateTime,
  formatDay,
  formatDayTime,
} from './dateFormats';

describe('dateFormats', () => {
  it('formats a timestamp the way the Journal feed shows it', () => {
    const out = formatDateTime('2026-07-02T14:30:00Z');
    expect(out).toMatch(/2026/);
    expect(out).toMatch(/Jul/);
  });

  it('formats a day heading with no time', () => {
    const out = formatDay('2026-07-02T00:00:00');
    expect(out).toMatch(/Jul 2, 2026/);
    expect(out).not.toMatch(/:/);
  });

  it('formats the food log row without a year', () => {
    expect(formatDayTime('2026-07-02T14:30:00Z')).not.toMatch(/2026/);
  });

  it('reuses one formatter instance per shape', () => {
    // The reason this module exists. Constructing an Intl.DateTimeFormat is
    // expensive, and the Journal feed was building one per row per render in
    // four places — the dominant cost of a feed re-render on the iPhone, which
    // happened on every keystroke in the compose box.
    expect(dateTimeFormat).toBe(dateTimeFormat);
    expect(dayFormat).not.toBe(dateTimeFormat);
  });
});
