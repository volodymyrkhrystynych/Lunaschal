// The weather stored on a journal or food entry (backend/weather/entry.py),
// as one short line for the entry's card. Pure, so it is tested in node; the
// iPhone app's EntryWeather.summary says the same thing the same way.
import { describeWeatherCode, moonPhase } from './weather';

export interface EntryWeather {
  hourTs: number;
  weatherCode: number;
  temperatureC: number;
  apparentC: number | null;
  humidityPct: number | null;
  windKmh: number | null;
  gustKmh: number | null;
  windy: boolean;
  isDay: boolean | null;
  latitude: number;
  longitude: number;
}

/** The column arrives as a JSON string, or null before the lookup has run. */
export function parseEntryWeather(
  raw: string | null | undefined
): EntryWeather | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw);
    return typeof value?.temperatureC === 'number' &&
      typeof value?.weatherCode === 'number'
      ? (value as EntryWeather)
      : null;
  } catch {
    return null;
  }
}

const degrees = (c: number) => `${Math.round(c)}°C`;

/** "⛅ Partly cloudy 4°C · feels −2°C · windy 34 km/h, gusts 58 · sun down" */
export function formatEntryWeather(weather: EntryWeather): string {
  const night = weather.isDay === false;
  const condition = describeWeatherCode(weather.weatherCode, {
    night,
    moon: moonPhase(new Date(weather.hourTs * 1000)),
  });
  const parts = [
    `${condition.icon} ${condition.label} ${degrees(weather.temperatureC)}`,
  ];
  if (weather.apparentC !== null && weather.apparentC !== undefined) {
    parts.push(`feels ${degrees(weather.apparentC)}`);
  }
  if (weather.windKmh !== null && weather.windKmh !== undefined) {
    const wind = `${Math.round(weather.windKmh)} km/h`;
    const gusts =
      weather.windy && weather.gustKmh !== null && weather.gustKmh !== undefined
        ? `, gusts ${Math.round(weather.gustKmh)}`
        : '';
    parts.push(weather.windy ? `windy ${wind}${gusts}` : `wind ${wind}`);
  }
  if (weather.isDay !== null && weather.isDay !== undefined) {
    parts.push(weather.isDay ? 'sun up' : 'sun down');
  }
  return parts.join(' · ');
}
