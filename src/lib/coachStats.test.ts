import { describe, expect, it } from 'vitest';
import type { CoachStat } from '@/hooks/api';
import { hourLabel, leadingFraming, percent } from './coachStats';

const stat = (arms: CoachStat['arms']): CoachStat => ({
  behavior: 'weight',
  label: 'Weigh-in',
  sent: 3,
  held: 1,
  skipped: 0,
  doneWhenSent: 0.75,
  doneWhenHeld: null,
  arms,
  slots: {},
});

describe('coach stats', () => {
  it('formats rates and hours', () => {
    expect(percent(0.756)).toBe('76%');
    expect(percent(null)).toBe('—');
    expect(hourLabel(7)).toBe('07:00');
  });

  it('names the leading framing, holding back included', () => {
    expect(leadingFraming(stat({}))).toBeNull();
    expect(
      leadingFraming(
        stat({ plain: { mean: 0.4, n: 3 }, identity: { mean: 0.7, n: 2 } })
      )?.arm
    ).toBe('identity');
    expect(
      leadingFraming(
        stat({ plain: { mean: 0.4, n: 3 }, none: { mean: 0.8, n: 5 } })
      )?.arm
    ).toBe('none');
  });
});
