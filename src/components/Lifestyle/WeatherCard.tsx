import { useEffect, useLayoutEffect, useRef } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/hooks/api';
import { currentPosition } from '@/lib/geo';
import { currentHourIndex, describeWeatherCode } from '@/lib/weather';
import { CARD } from './card';

/**
 * The day's weather, sourced from wherever the device actually is.
 *
 * **Geolocation is requested on every mount**, not just once a day: the
 * Lifestyle tab visit is the only moment this app can ever get a real fix (no
 * background daemon polls for one — see plans/weather-tracker.md), so a fresh
 * mount is also how the location log gets updated as the day goes on.
 * `currentPosition` resolves `null` on denial/timeout rather than throwing, so
 * a refused permission simply skips the location update — `GET /today`
 * already guarantees the best available data (last-known location, then the
 * configured default, then nothing).
 *
 * The first visit of a new Lunaschal day is also what triggers that day's
 * initial forecast fetch — `ensure_today` on the backend has no rows yet, so
 * the `today` query itself performs the sync. There is deliberately no
 * separate "start of day" job.
 */
export function WeatherCard() {
  const queryClient = useQueryClient();
  const stripRef = useRef<HTMLDivElement>(null);

  const { data } = useQuery({
    queryKey: ['lifestyle', 'weather', 'today'],
    queryFn: api.lifestyle.weather.today,
  });

  const updateLocation = useMutation({
    mutationFn: ({
      latitude,
      longitude,
    }: {
      latitude: number;
      longitude: number;
    }) => api.lifestyle.weather.updateLocation(latitude, longitude),
    onSuccess: result =>
      queryClient.setQueryData(['lifestyle', 'weather', 'today'], result),
  });

  useEffect(() => {
    // Only on mount — a fresh fix per Lifestyle tab visit, not per render.
    currentPosition().then(coords => {
      if (coords) updateLocation.mutate(coords);
    });
  }, []);

  const hours = data?.hours ?? [];
  const nowIndex = currentHourIndex(hours);
  const current = nowIndex >= 0 ? hours[nowIndex] : null;

  // Hours render oldest-to-newest across the day; scroll the strip to "now"
  // on mount/data-arrival, the same pattern SelfieCard/ActivityHeatmap use.
  useLayoutEffect(() => {
    const el = stripRef.current;
    if (!el || nowIndex < 0) return;
    const cell = el.children[nowIndex] as HTMLElement | undefined;
    if (cell) el.scrollLeft = cell.offsetLeft - el.clientWidth / 2;
  }, [nowIndex]);

  return (
    <section className={CARD}>
      <h2 className="text-lg font-semibold text-[var(--color-text)] mb-3">
        Weather
      </h2>

      {data?.location === null ? (
        <p className="text-sm text-[var(--color-text-muted)]">
          No location yet — allow location access, or set a default location in
          Settings.
        </p>
      ) : current ? (
        <>
          <div className="flex items-center gap-3">
            <span className="text-3xl" aria-hidden>
              {describeWeatherCode(current.weatherCode).icon}
            </span>
            <div>
              <div className="text-2xl font-semibold text-[var(--color-text)] tabular-nums">
                {Math.round(current.temperatureC)}°C
              </div>
              <div className="text-sm text-[var(--color-text-muted)]">
                {describeWeatherCode(current.weatherCode).label}
                {current.wetBulbC !== null &&
                  ` · wet bulb ${Math.round(current.wetBulbC)}°C`}
              </div>
            </div>
          </div>

          <div ref={stripRef} className="mt-3 flex gap-2 overflow-x-auto pb-1">
            {hours.map((hour, i) => (
              <div
                key={hour.id}
                className={`shrink-0 text-center px-1.5 py-1 rounded ${
                  i === nowIndex ? 'bg-[var(--color-primary)]/20' : ''
                } ${!hour.isActual ? 'opacity-70' : ''}`}
              >
                <div className="text-[10px] text-[var(--color-text-muted)]">
                  {new Date(hour.hourTs).getHours()}:00
                </div>
                <div className="text-base" aria-hidden>
                  {describeWeatherCode(hour.weatherCode).icon}
                </div>
                <div className="text-xs text-[var(--color-text)] tabular-nums">
                  {Math.round(hour.temperatureC)}°
                </div>
              </div>
            ))}
          </div>
        </>
      ) : (
        <p className="text-sm text-[var(--color-text-muted)]">
          No forecast yet.
        </p>
      )}
    </section>
  );
}
