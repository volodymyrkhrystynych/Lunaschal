import { describe, expect, it } from 'vitest';
import type { PomodoroDay, PomodoroSession } from '@/hooks/api';
import { focusBars, focusTotals, sessionLabel } from './pomodoro';

const day = (
  date: string,
  focusMinutes: number,
  completedBlocks = 0
): PomodoroDay => ({
  date,
  focusMinutes,
  breakMinutes: 0,
  timeoutMinutes: 0,
  completedBlocks,
});

describe('focusBars', () => {
  it('keeps a slot for an empty day', () => {
    const bars = focusBars(
      [day('a', 50), day('b', 0), day('c', 25)],
      90,
      40,
      0
    );
    expect(bars.map(b => b.x)).toEqual([0, 30, 60]);
    expect(bars[0].height).toBe(40);
    expect(bars[1].height).toBe(0);
    expect(bars[2].height).toBe(20);
  });

  it('never stretches a short day to full height', () => {
    const [bar] = focusBars([day('a', 5)], 10, 50, 0);
    expect(bar.height).toBe(10);
  });
});

describe('focusTotals', () => {
  it('adds up minutes and finished blocks', () => {
    expect(focusTotals([day('a', 50, 2), day('b', 12, 0)])).toEqual({
      minutes: 62,
      blocks: 2,
    });
  });
});

describe('sessionLabel', () => {
  const s: PomodoroSession = {
    id: 'x',
    kind: 'work',
    date: '2026-07-20',
    startedAt: '2026-07-20T13:00:00+00:00',
    endedAt: '2026-07-20T13:25:00+00:00',
    plannedSeconds: 1500,
    completed: true,
    createdAt: '2026-07-20T13:25:00+00:00',
  };

  it('names a finished run by its length', () => {
    expect(sessionLabel(s)).toBe('Focus · 25 min');
    expect(sessionLabel({ ...s, kind: 'timeout', plannedSeconds: 600 })).toBe(
      'Timeout · 10 min'
    );
  });

  it('says how far a cancelled run got', () => {
    expect(
      sessionLabel({
        ...s,
        endedAt: '2026-07-20T13:12:00+00:00',
        completed: false,
      })
    ).toBe('Focus · 12 of 25 min (cancelled)');
  });
});
