import { describe, it, expect } from 'vitest';
import {
  describeWeatherCode,
  currentHourIndex,
  type WeatherHour,
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
