import { describe, expect, it } from 'vitest';
import { readingLayout, type CalendarReadingSpan } from './calendarReading';
import type { SleepDay } from './sleep';

const date = '2026-07-13';
const at = (clock: string, day = date) =>
  new Date(`${day}T${clock}:00`).getTime() / 1000;
const sleep: SleepDay = {
  date,
  wakeAt: at('08:00'),
  sleepAt: at('01:00', '2026-07-14'),
  wakeSource: 'manual',
  sleepSource: 'manual',
  previousSleepAt: null,
  nextWakeAt: null,
};
const span = (
  id: string,
  startedAt: number,
  endedAt: number
): CalendarReadingSpan => ({
  id,
  title: 'Lighthouse',
  chapterNumber: 3,
  startedAt,
  endedAt,
});

describe('reading rail geometry', () => {
  it('extends from waking to past-midnight sleep and preserves the gaps', () => {
    const layout = readingLayout(date, sleep, [
      span('a', at('09:00'), at('09:30')),
      span('b', at('00:00', '2026-07-14'), at('00:30', '2026-07-14')),
    ]);
    expect([layout.start, layout.end]).toEqual([240, 1260]);
    expect(layout.spans.map(s => [s.start, s.end])).toEqual([
      [300, 330],
      [1200, 1230],
    ]);
  });
  it('clips fills at wake and sleep and drops spans outside the rail', () => {
    const layout = readingLayout(date, sleep, [
      span('a', at('07:30'), at('08:30')),
      span('b', at('00:30', '2026-07-14'), at('02:00', '2026-07-14')),
      span('c', at('06:00'), at('07:00')),
    ]);
    expect(layout.spans.map(s => [s.start, s.end])).toEqual([
      [240, 270],
      [1230, 1260],
    ]);
  });
  it('uses day edges for unknown boundaries without inventing sleep times', () => {
    expect(
      readingLayout(date, { ...sleep, wakeAt: null, sleepAt: null }, [])
    ).toEqual({ start: 0, end: 1440, spans: [] });
  });
});
