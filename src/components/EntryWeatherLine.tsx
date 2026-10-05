import { formatEntryWeather, parseEntryWeather } from '../lib/entryWeather';

/** The weather an entry was written in, as one muted line; nothing until the
 * server's background lookup has filled it in. */
export function EntryWeatherLine({
  weather,
  className = '',
}: {
  weather: string | null | undefined;
  className?: string;
}) {
  const parsed = parseEntryWeather(weather);
  if (!parsed) return null;
  return (
    <span
      className={`text-xs text-[var(--color-text-muted)] ${className}`}
      data-testid="entry-weather"
    >
      {formatEntryWeather(parsed)}
    </span>
  );
}
