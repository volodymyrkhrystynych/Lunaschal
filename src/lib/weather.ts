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

export interface MoonPhaseInfo {
  /** 0 = new moon, 4 = full moon. */
  index: number;
  name: string;
  emoji: string;
}

const MOON_PHASES: MoonPhaseInfo[] = [
  { index: 0, name: 'New moon', emoji: '🌑' },
  { index: 1, name: 'Waxing crescent', emoji: '🌒' },
  { index: 2, name: 'First quarter', emoji: '🌓' },
  { index: 3, name: 'Waxing gibbous', emoji: '🌔' },
  { index: 4, name: 'Full moon', emoji: '🌕' },
  { index: 5, name: 'Waning gibbous', emoji: '🌖' },
  { index: 6, name: 'Last quarter', emoji: '🌗' },
  { index: 7, name: 'Waning crescent', emoji: '🌘' },
];

const SYNODIC_MONTH_DAYS = 29.530588853;
// A known new moon, UTC. Only the fractional position within a synodic month
// matters, so any confirmed new moon works as the reference epoch.
const REFERENCE_NEW_MOON_MS = Date.UTC(2000, 0, 6, 18, 14, 0);
const MS_PER_DAY = 86_400_000;

/** Pure, deterministic synodic-month calculation — no astronomy library
 * needed since only the phase (not rise/set/illumination angle) is wanted. */
export function moonPhase(date: Date = new Date()): MoonPhaseInfo {
  const daysSince = (date.getTime() - REFERENCE_NEW_MOON_MS) / MS_PER_DAY;
  const cycles = daysSince / SYNODIC_MONTH_DAYS;
  const fraction = cycles - Math.floor(cycles);
  const index = Math.floor(fraction * 8) % 8;
  return MOON_PHASES[index];
}

/** Whether `hourTs` falls outside [sunrise, sunset) for the day it belongs
 * to. Never claims night without real sun data — a missing sunrise/sunset
 * (no location yet, or a sync that hasn't run) resolves to "day" so icons
 * fall back to their existing, always-correct-enough day form. */
export function isNight(
  hourTs: string,
  sunriseTs: string | null,
  sunsetTs: string | null
): boolean {
  if (sunriseTs === null || sunsetTs === null) return false;
  const t = new Date(hourTs).getTime();
  return t < new Date(sunriseTs).getTime() || t >= new Date(sunsetTs).getTime();
}

/** Never throws — an unmapped code (a WMO value Open-Meteo starts emitting
 * later, or a bad row) sorts safely into "Unknown" rather than crashing the
 * card.
 *
 * `opts.night` swaps in an icon accurate to the real moon phase for clear/
 * mainly-clear conditions, and a moon+cloud combo for partly cloudy — codes
 * that already show a cloud/precipitation icon (overcast and up) are left
 * unchanged, since cloud cover looks the same regardless of the hour. */
export function describeWeatherCode(
  code: number,
  opts?: { night?: boolean; moon?: MoonPhaseInfo }
): WeatherCondition {
  const base = WMO_CODES[code] ?? UNKNOWN_CONDITION;
  if (!opts?.night) return base;

  const moon = opts.moon ?? moonPhase();
  if (code === 0 || code === 1) return { label: base.label, icon: moon.emoji };
  if (code === 2) return { label: base.label, icon: `${moon.emoji}☁️` };
  return base;
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
