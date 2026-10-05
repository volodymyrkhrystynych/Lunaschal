import { describe, expect, it } from 'vitest';
import {
  formatEntryWeather,
  parseEntryWeather,
  type EntryWeather,
} from './entryWeather';

const base: EntryWeather = {
  hourTs: 1_790_000_000,
  weatherCode: 3,
  temperatureC: 3.2,
  apparentC: -2.6,
  humidityPct: 70,
  windKmh: 34.4,
  gustKmh: 58,
  windy: true,
  isDay: true,
  latitude: 43.6,
  longitude: -79.4,
};

describe('parseEntryWeather', () => {
  it('reads the stored JSON', () => {
    expect(parseEntryWeather(JSON.stringify(base))).toEqual(base);
  });

  it('is null before the lookup ran, or for anything malformed', () => {
    expect(parseEntryWeather(null)).toBeNull();
    expect(parseEntryWeather(undefined)).toBeNull();
    expect(parseEntryWeather('not json')).toBeNull();
    expect(parseEntryWeather('{"temperatureC": "cold"}')).toBeNull();
  });
});

describe('formatEntryWeather', () => {
  it('says the conditions, the feels-like, the wind and the sun', () => {
    expect(formatEntryWeather(base)).toBe(
      '☁️ Overcast 3°C · feels -3°C · windy 34 km/h, gusts 58 · sun up'
    );
  });

  it('mentions calm wind without gusts', () => {
    expect(
      formatEntryWeather({ ...base, windy: false, windKmh: 8, gustKmh: 15 })
    ).toContain('wind 8 km/h · ');
  });

  it('shows the moon on a clear night', () => {
    const line = formatEntryWeather({ ...base, weatherCode: 0, isDay: false });
    expect(line).toMatch(/^[🌑🌒🌓🌔🌕🌖🌗🌘] Clear/u);
    expect(line.endsWith('sun down')).toBe(true);
  });

  it('leaves out what an older snapshot did not record', () => {
    expect(
      formatEntryWeather({
        ...base,
        apparentC: null,
        windKmh: null,
        isDay: null,
      })
    ).toBe('☁️ Overcast 3°C');
  });
});
