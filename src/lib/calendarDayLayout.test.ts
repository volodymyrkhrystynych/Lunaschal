import { describe, it, expect } from 'vitest';
import {
  computeOverlapDepth,
  eventHeightPx,
  eventTopPx,
  minutesToTime,
  moveEventByMinutes,
  MIN_DURATION_MINUTES,
  MINUTES_PER_DAY,
  pxDeltaToMinutes,
  resizeEventEndByMinutes,
  snapMinutes,
  timeToMinutes,
} from './calendarDayLayout';

describe('timeToMinutes / minutesToTime', () => {
  it('round-trips a plain HH:MM', () => {
    expect(timeToMinutes('09:30')).toBe(570);
    expect(minutesToTime(570)).toBe('09:30');
  });

  it('handles midnight and the last minute of the day', () => {
    expect(timeToMinutes('00:00')).toBe(0);
    expect(minutesToTime(0)).toBe('00:00');
    expect(minutesToTime(MINUTES_PER_DAY - 1)).toBe('23:59');
  });

  it('tolerates an HH:MM:SS input, matching what <input type=time step=1> emits', () => {
    expect(timeToMinutes('14:05:30')).toBe(845);
  });

  it('clamps minutesToTime to the day', () => {
    expect(minutesToTime(-10)).toBe('00:00');
    expect(minutesToTime(MINUTES_PER_DAY + 100)).toBe('23:59');
  });
});

describe('snapMinutes', () => {
  it('snaps to the default 5-minute grid', () => {
    expect(snapMinutes(12)).toBe(10);
    expect(snapMinutes(13)).toBe(15);
  });

  it('snaps to an explicit step', () => {
    expect(snapMinutes(22, 10)).toBe(20);
    expect(snapMinutes(26, 10)).toBe(30);
  });
});

describe('eventTopPx / eventHeightPx', () => {
  it('positions and sizes proportionally to minutes', () => {
    expect(eventTopPx(90, 2)).toBe(180);
    expect(eventHeightPx(90, 150, 2)).toBe(120);
  });

  it('floors height at MIN_DURATION_MINUTES', () => {
    // A 5-minute event should still render at the readable floor, not
    // shrink to an unusable sliver.
    expect(eventHeightPx(100, 105, 1)).toBe(MIN_DURATION_MINUTES);
  });
});

describe('moveEventByMinutes', () => {
  it('shifts both ends by the same delta, preserving duration', () => {
    const moved = moveEventByMinutes(
      { startMinutes: 100, endMinutes: 160 },
      30
    );
    expect(moved).toEqual({ startMinutes: 130, endMinutes: 190 });
  });

  it('clamps at the start of the day', () => {
    const moved = moveEventByMinutes(
      { startMinutes: 10, endMinutes: 70 },
      -100
    );
    expect(moved.startMinutes).toBe(0);
    expect(moved.endMinutes).toBe(60); // duration preserved
  });

  it('clamps at the end of the day', () => {
    const moved = moveEventByMinutes(
      { startMinutes: MINUTES_PER_DAY - 60, endMinutes: MINUTES_PER_DAY },
      100
    );
    expect(moved.endMinutes).toBe(MINUTES_PER_DAY);
    expect(moved.startMinutes).toBe(MINUTES_PER_DAY - 60);
  });
});

describe('resizeEventEndByMinutes', () => {
  it('extends the end, leaving the start fixed', () => {
    const resized = resizeEventEndByMinutes(
      { startMinutes: 100, endMinutes: 160 },
      30
    );
    expect(resized).toEqual({ startMinutes: 100, endMinutes: 190 });
  });

  it('refuses to shrink below MIN_DURATION_MINUTES', () => {
    // Dragging the handle up past the start must not produce a
    // zero-or-negative-length event.
    const resized = resizeEventEndByMinutes(
      { startMinutes: 100, endMinutes: 160 },
      -1000
    );
    expect(resized.endMinutes - resized.startMinutes).toBe(
      MIN_DURATION_MINUTES
    );
  });

  it('clamps at midnight', () => {
    const resized = resizeEventEndByMinutes(
      { startMinutes: MINUTES_PER_DAY - 30, endMinutes: MINUTES_PER_DAY - 10 },
      1000
    );
    expect(resized.endMinutes).toBe(MINUTES_PER_DAY);
  });
});

describe('pxDeltaToMinutes', () => {
  it('converts pixel travel to a snapped minute delta', () => {
    // 2px/minute, 23px of travel -> 11.5 minutes, snapped to the nearest 5.
    expect(pxDeltaToMinutes(23, 2)).toBe(10);
  });

  it('can use a custom snap step', () => {
    expect(pxDeltaToMinutes(22, 1, 10)).toBe(20);
  });
});

describe('computeOverlapDepth', () => {
  it('gives a lone event depth 0', () => {
    const depths = computeOverlapDepth([
      { id: 'a', startMinutes: 0, endMinutes: 60 },
    ]);
    expect(depths.get('a')).toBe(0);
  });

  it('gives two non-overlapping events both depth 0', () => {
    const depths = computeOverlapDepth([
      { id: 'a', startMinutes: 0, endMinutes: 60 },
      { id: 'b', startMinutes: 60, endMinutes: 120 },
    ]);
    expect(depths.get('a')).toBe(0);
    expect(depths.get('b')).toBe(0);
  });

  it('nests a shorter overlapping event inside a longer one', () => {
    // The 2-hour event should stay the outer frame; the 1-hour event nests
    // inside it — matching the confirmed picture-in-picture mockup.
    const depths = computeOverlapDepth([
      { id: 'short', startMinutes: 30, endMinutes: 90 }, // 1h, inside the long one
      { id: 'long', startMinutes: 0, endMinutes: 120 }, // 2h
    ]);
    expect(depths.get('long')).toBe(0);
    expect(depths.get('short')).toBe(1);
  });

  it('gives three mutually-overlapping events three distinct depths', () => {
    const depths = computeOverlapDepth([
      { id: 'a', startMinutes: 0, endMinutes: 180 },
      { id: 'b', startMinutes: 30, endMinutes: 150 },
      { id: 'c', startMinutes: 60, endMinutes: 120 },
    ]);
    const values = new Set([depths.get('a'), depths.get('b'), depths.get('c')]);
    expect(values.size).toBe(3);
    // Longest stays outermost.
    expect(depths.get('a')).toBe(0);
  });

  it('reuses a depth once its occupant has ended', () => {
    // b overlaps a; c starts after a ends but overlaps nothing live at that
    // depth, so it can reuse depth 0 rather than escalating forever.
    const depths = computeOverlapDepth([
      { id: 'a', startMinutes: 0, endMinutes: 60 },
      { id: 'b', startMinutes: 30, endMinutes: 90 },
      { id: 'c', startMinutes: 100, endMinutes: 160 },
    ]);
    expect(depths.get('a')).toBe(0);
    expect(depths.get('b')).toBe(1);
    expect(depths.get('c')).toBe(0);
  });
});
