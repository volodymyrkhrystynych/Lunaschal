import { describe, expect, it } from 'vitest';
import type { GamingStatus } from '../hooks/api';
import { acceptableReason, gamingSummary, toggleDay } from './gamingLock';

function status(over: Partial<GamingStatus> = {}): GamingStatus {
  return {
    day: '2026-11-11',
    applies: true,
    locked: true,
    unlockedAt: null,
    method: null,
    reason: null,
    ratingAtUnlock: null,
    workEnd: null,
    settings: { enabled: true, days: [0, 1, 2, 3, 4], patterns: ['steam'] },
    ...over,
  };
}

describe('gamingSummary', () => {
  it('says off, not a lock day, or locked with the work end', () => {
    expect(
      gamingSummary(
        status({ settings: { enabled: false, days: [], patterns: [] } })
      )
    ).toBe('Off');
    expect(gamingSummary(status({ applies: false, locked: false }))).toBe(
      'Not a lock day — games are open'
    );
    expect(gamingSummary(status({ workEnd: '17:30' }))).toBe(
      "Locked until you tell the chat you're done with work (work ends 17:30)"
    );
  });

  it('names how and when it was unlocked', () => {
    const at = new Date(2026, 10, 11, 18, 5).getTime() / 1000;
    expect(
      gamingSummary(
        status({
          locked: false,
          method: 'review',
          unlockedAt: at,
          workEnd: '17:00',
        })
      )
    ).toBe('Unlocked after the review at 18:05 · work ended 17:00');
    expect(
      gamingSummary(
        status({ locked: false, method: 'override', unlockedAt: at })
      )
    ).toBe('Unlocked without the review at 18:05');
  });
});

describe('override reason and weekdays', () => {
  it('needs ten characters of reason', () => {
    expect(acceptableReason('  bored   ')).toBe(false);
    expect(acceptableReason('Friends online for a raid')).toBe(true);
  });

  it('toggles a weekday and keeps the list sorted', () => {
    expect(toggleDay([0, 1, 4], 2)).toEqual([0, 1, 2, 4]);
    expect(toggleDay([0, 1, 4], 1)).toEqual([0, 4]);
  });
});
