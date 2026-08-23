import { describe, it, expect } from 'vitest';
import {
  describeWeatherCode,
  currentHourIndex,
  moonPhase,
  isNight,
  type WeatherHour,
  type MoonPhaseInfo,
} from './weather';

describe('describeWeatherCode', () => {
  it.each([
    [0, 'Clear'],
    [2, 'Partly cloudy'],
    [45, 'Fog'],
    [61, 'Light rain'],
    [71, 'Light snow'],
    [95, 'Thunderstorm'],
  ])('maps code %i to %s', (code, label) => {
    expect(describeWeatherCode(code).label).toBe(label);
  });

  it('falls back to Unknown for an unmapped code, rather than throwing', () => {
    expect(describeWeatherCode(12345)).toEqual({
      label: 'Unknown',
      icon: '❔',
    });
  });

  const FULL_MOON: MoonPhaseInfo = {
    index: 4,
    name: 'Full moon',
    emoji: '🌕',
  };

  it.each([
    [0, '🌕'],
    [1, '🌕'],
  ])(
    'swaps the moon-phase emoji in for code %i at night',
    (code, expectedIcon) => {
      expect(
        describeWeatherCode(code, { night: true, moon: FULL_MOON }).icon
      ).toBe(expectedIcon);
    }
  );

  it('combines the moon-phase emoji with the cloud emoji for partly cloudy at night', () => {
    expect(describeWeatherCode(2, { night: true, moon: FULL_MOON }).icon).toBe(
      '🌕☁️'
    );
  });

  it.each([3, 45, 61, 71, 95])(
    'leaves code %i unchanged at night — clouds obscure the sky either way',
    code => {
      const day = describeWeatherCode(code);
      const night = describeWeatherCode(code, { night: true, moon: FULL_MOON });
      expect(night).toEqual(day);
    }
  );
});

describe('moonPhase', () => {
  // 2000-01-06T18:14Z is a documented real new moon, used here as the
  // function's own reference epoch — so this also verifies the epoch wiring.
  const REFERENCE_NEW_MOON_MS = Date.UTC(2000, 0, 6, 18, 14, 0);
  const SYNODIC_MONTH_DAYS = 29.530588853;
  const MS_PER_DAY = 86_400_000;

  it('reports a new moon at the reference epoch', () => {
    expect(moonPhase(new Date(REFERENCE_NEW_MOON_MS)).name).toBe('New moon');
  });

  it('reports a full moon around the middle of the synodic month', () => {
    // 0.55 cycles sits safely inside the "Full moon" eighth (0.5-0.625) —
    // exactly 0.5 is a floating-point boundary with the preceding eighth.
    const midCycle = new Date(
      REFERENCE_NEW_MOON_MS + SYNODIC_MONTH_DAYS * 0.55 * MS_PER_DAY
    );
    expect(moonPhase(midCycle).name).toBe('Full moon');
  });

  it('cycles back to a new moon just past a full synodic month later', () => {
    // 1.05 cycles sits safely inside the next "New moon" eighth — exactly
    // 1.0 is a floating-point boundary with the preceding eighth.
    const nextCycle = new Date(
      REFERENCE_NEW_MOON_MS + SYNODIC_MONTH_DAYS * 1.05 * MS_PER_DAY
    );
    expect(moonPhase(nextCycle).name).toBe('New moon');
  });

  it('is deterministic for the same date', () => {
    const d = new Date('2026-03-15T00:00:00Z');
    expect(moonPhase(d)).toEqual(moonPhase(d));
  });
});

describe('isNight', () => {
  const sunrise = '2026-08-17T10:00:00Z';
  const sunset = '2026-08-17T22:00:00Z';

  it('is false during the day', () => {
    expect(isNight('2026-08-17T14:00:00Z', sunrise, sunset)).toBe(false);
  });

  it('is true before sunrise', () => {
    expect(isNight('2026-08-17T05:00:00Z', sunrise, sunset)).toBe(true);
  });

  it('is true after sunset', () => {
    expect(isNight('2026-08-17T23:00:00Z', sunrise, sunset)).toBe(true);
  });

  it('is never true when sun times are unknown', () => {
    expect(isNight('2026-08-17T23:00:00Z', null, null)).toBe(false);
    expect(isNight('2026-08-17T23:00:00Z', sunrise, null)).toBe(false);
    expect(isNight('2026-08-17T23:00:00Z', null, sunset)).toBe(false);
  });
});

describe('currentHourIndex', () => {
  const hour = (iso: string, overrides: Partial<WeatherHour> = {}) =>
    ({
      id: iso,
      dayKey: '2026-08-17',
      hourTs: iso,
      weatherCode: 0,
      temperatureC: 20,
      wetBulbC: 15,
      humidityPct: 50,
      isActual: false,
      latitude: 0,
      longitude: 0,
      locationSource: 'geolocation',
      ...overrides,
    }) as WeatherHour;

  it('returns -1 for an empty list', () => {
    expect(currentHourIndex([])).toBe(-1);
  });

  it('picks the latest hour that has already started', () => {
    const hours = [
      hour('2026-08-17T10:00:00Z'),
      hour('2026-08-17T11:00:00Z'),
      hour('2026-08-17T12:00:00Z'),
    ];
    const now = new Date('2026-08-17T11:30:00Z');
    expect(currentHourIndex(hours, now)).toBe(1);
  });

  it('falls back to the first (soonest upcoming) row when every hour is still in the future', () => {
    const hours = [hour('2026-08-17T10:00:00Z'), hour('2026-08-17T11:00:00Z')];
    const now = new Date('2026-08-17T05:00:00Z');
    expect(currentHourIndex(hours, now)).toBe(0);
  });
});
