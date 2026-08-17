// Pure helpers for the Lifestyle weather card — WMO weather-code lookup and
// "which hour is now" — extracted so they're testable in the node environment
// (no jsdom), the same way src/lib/lifestyle.ts is.

export interface WeatherHour {
  id: string;
  dayKey: string;
  hourTs: string;
  weatherCode: number;
  temperatureC: number;
  wetBulbC: number | null;
  humidityPct: number | null;
  isActual: boolean;
  latitude: number;
  longitude: number;
  locationSource: 'geolocation' | 'default';
}

export interface WeatherCondition {
  label: string;
  icon: string;
}

// Open-Meteo's hourly `weather_code` is WMO code table 4677. Only the codes
// Open-Meteo actually emits are listed.
export const WMO_CODES: Record<number, WeatherCondition> = {
  0: { label: 'Clear', icon: '☀️' },
  1: { label: 'Mainly clear', icon: '🌤️' },
  2: { label: 'Partly cloudy', icon: '⛅' },
  3: { label: 'Overcast', icon: '☁️' },
  45: { label: 'Fog', icon: '🌫️' },
  48: { label: 'Freezing fog', icon: '🌫️' },
  51: { label: 'Light drizzle', icon: '🌦️' },
  53: { label: 'Drizzle', icon: '🌦️' },
  55: { label: 'Dense drizzle', icon: '🌦️' },
  56: { label: 'Freezing drizzle', icon: '🌧️' },
  57: { label: 'Dense freezing drizzle', icon: '🌧️' },
  61: { label: 'Light rain', icon: '🌧️' },
  63: { label: 'Rain', icon: '🌧️' },
  65: { label: 'Heavy rain', icon: '🌧️' },
  66: { label: 'Freezing rain', icon: '🌨️' },
  67: { label: 'Heavy freezing rain', icon: '🌨️' },
  71: { label: 'Light snow', icon: '🌨️' },
  73: { label: 'Snow', icon: '🌨️' },
  75: { label: 'Heavy snow', icon: '❄️' },
  77: { label: 'Snow grains', icon: '❄️' },
  80: { label: 'Light showers', icon: '🌦️' },
  81: { label: 'Showers', icon: '🌦️' },
  82: { label: 'Violent showers', icon: '⛈️' },
  85: { label: 'Snow showers', icon: '🌨️' },
  86: { label: 'Heavy snow showers', icon: '🌨️' },
  95: { label: 'Thunderstorm', icon: '⛈️' },
  96: { label: 'Thunderstorm with hail', icon: '⛈️' },
  99: { label: 'Severe thunderstorm with hail', icon: '⛈️' },
};

const UNKNOWN_CONDITION: WeatherCondition = { label: 'Unknown', icon: '❔' };

/** Never throws — an unmapped code (a WMO value Open-Meteo starts emitting
 * later, or a bad row) sorts safely into "Unknown" rather than crashing the
 * card. */
export function describeWeatherCode(code: number): WeatherCondition {
  return WMO_CODES[code] ?? UNKNOWN_CONDITION;
}

/** Index of the hour row that best represents "now": the latest one that has
 * already started. Falls back to the first (soonest upcoming) row if every
 * hour is still in the future, and -1 for an empty list. */
export function currentHourIndex(
  hours: WeatherHour[],
  now: Date = new Date()
): number {
  if (hours.length === 0) return -1;
  const nowMs = now.getTime();
  let best = 0;
  for (let i = 0; i < hours.length; i++) {
    if (new Date(hours[i].hourTs).getTime() <= nowMs) best = i;
  }
  return best;
}
