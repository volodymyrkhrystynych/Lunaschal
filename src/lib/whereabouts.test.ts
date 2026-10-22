import { describe, expect, it } from 'vitest';
import { formatStay, whereaboutsLine } from './whereabouts';

const local = (h: number, m: number) =>
  new Date(2026, 6, 8, h, m).toISOString();

describe('whereabouts', () => {
  it('formats a stay in hours and minutes', () => {
    expect(formatStay(45 * 60)).toBe('45m');
    expect(formatStay(3 * 3600)).toBe('3h');
    expect(formatStay(7 * 3600 + 20 * 60)).toBe('7h 20m');
  });

  it('lists each work by time, then when the user got home', () => {
    expect(
      whereaboutsLine({
        home: { seconds: 9000, lastArrival: local(18, 40), here: true },
        work: [
          {
            name: 'Office',
            seconds: 7 * 3600 + 20 * 60,
            firstArrival: null,
            lastDeparture: null,
          },
          {
            name: 'Cowork',
            seconds: 3600,
            firstArrival: null,
            lastDeparture: null,
          },
        ],
      })
    ).toBe('7h 20m at Office · 1h at Cowork · home from 18:40');
  });

  it('says time at home when the user left again', () => {
    expect(
      whereaboutsLine({
        home: { seconds: 4 * 3600, lastArrival: local(9, 0), here: false },
        work: [],
      })
    ).toBe('4h at home');
  });

  it('says nothing when nothing matched', () => {
    expect(whereaboutsLine(null)).toBeNull();
    expect(whereaboutsLine(undefined)).toBeNull();
    expect(whereaboutsLine({ home: null, work: [] })).toBeNull();
  });
});
