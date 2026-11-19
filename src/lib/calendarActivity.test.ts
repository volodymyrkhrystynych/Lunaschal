import { expect, it } from 'vitest';
import { activityFill, activityLayout } from './calendarActivity';
import type { SleepDay } from './sleep';

const date = '2026-07-13';
const at = (clock: string, day = date) =>
  new Date(`${day}T${clock}:00`).getTime() / 1000;
const sleep = (wake: number | null, bed: number | null): SleepDay => ({
  date,
  wakeAt: wake,
  sleepAt: bed,
  wakeSource: null,
  sleepSource: null,
  previousSleepAt: null,
  nextWakeAt: null,
});

it('places spans in minutes from 4am, trimmed to the waking day', () => {
  const layout = activityLayout(
    date,
    sleep(at('08:00'), at('01:00', '2026-07-14')),
    [
      {
        id: 'a',
        state: 'leisure',
        startedAt: at('04:00'),
        endedAt: at('09:00'),
      },
      { id: 'b', state: 'work', startedAt: at('09:00'), endedAt: at('12:00') },
      {
        id: 'c',
        state: 'mix',
        startedAt: at('12:00'),
        endedAt: at('03:00', '2026-07-14'),
      },
    ]
  );
  expect([layout.start, layout.end]).toEqual([240, 1260]);
  expect(layout.spans.map(s => [s.state, s.start, s.end])).toEqual([
    ['leisure', 240, 300],
    ['work', 300, 480],
    ['mix', 480, 1260],
  ]);
});

it('runs edge to edge when wake and sleep are unknown, and drops what falls outside', () => {
  const layout = activityLayout(date, sleep(null, at('10:00')), [
    { id: 'a', state: 'work', startedAt: at('11:00'), endedAt: at('12:00') },
  ]);
  expect([layout.start, layout.end, layout.spans]).toEqual([0, 360, []]);
});

it('fills work and leisure with their calendar colours and stripes mix with both', () => {
  expect(activityFill('work')).toBe('#27a164');
  expect(activityFill('leisure')).toBe('#d9b382');
  expect(activityFill('mix')).toContain('#27a164');
  expect(activityFill('mix')).toContain('#d9b382');
});
