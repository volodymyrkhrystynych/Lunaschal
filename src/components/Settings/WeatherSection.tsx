import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';

/** Fallback location for the Lifestyle weather card, used only until a real
 * geolocation fix has been logged. Raw lat/lon rather than a place-name
 * lookup — no geocoding dependency, same as the food-entry location fields. */
export function WeatherSection() {
  const queryClient = useQueryClient();
  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.settings.get,
  });
  const [lat, setLat] = useState('');
  const [lon, setLon] = useState('');
  const [label, setLabel] = useState('');

  useEffect(() => {
    if (!settings) return;
    setLat(
      settings.weatherDefaultLat != null
        ? String(settings.weatherDefaultLat)
        : ''
    );
    setLon(
      settings.weatherDefaultLon != null
        ? String(settings.weatherDefaultLon)
        : ''
    );
    setLabel(settings.weatherDefaultLabel ?? '');
  }, [settings]);

  const save = useMutation({
    mutationFn: (data: {
      weatherDefaultLat?: number;
      weatherDefaultLon?: number;
      weatherDefaultLabel?: string;
    }) => api.settings.updateAI(data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['settings'] }),
  });

  const commitCoords = () => {
    const latNum = parseFloat(lat);
    const lonNum = parseFloat(lon);
    if (Number.isFinite(latNum) && Number.isFinite(lonNum)) {
      save.mutate({ weatherDefaultLat: latNum, weatherDefaultLon: lonNum });
    }
  };

  return (
    <section className="mb-8">
      <h2 className="text-lg font-medium text-[var(--color-text)] mb-4">
        Weather
      </h2>
      <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10 space-y-4">
        <p className="text-sm text-[var(--color-text-muted)]">
          Used by the Lifestyle tab's weather card until it has a real
          geolocation fix for the day.
        </p>
        <div className="flex gap-3">
          <div>
            <label className="text-sm text-[var(--color-text-muted)]">
              Latitude
            </label>
            <input
              type="number"
              step="any"
              value={lat}
              onChange={e => setLat(e.target.value)}
              onBlur={commitCoords}
              className="mt-1 w-32 bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
            />
          </div>
          <div>
            <label className="text-sm text-[var(--color-text-muted)]">
              Longitude
            </label>
            <input
              type="number"
              step="any"
              value={lon}
              onChange={e => setLon(e.target.value)}
              onBlur={commitCoords}
              className="mt-1 w-32 bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
            />
          </div>
        </div>
        <div>
          <label className="text-sm text-[var(--color-text-muted)]">
            Label (optional)
          </label>
          <input
            type="text"
            value={label}
            onChange={e => setLabel(e.target.value)}
            onBlur={() => save.mutate({ weatherDefaultLabel: label })}
            placeholder="Home"
            className="mt-1 w-full bg-[var(--color-bg)] text-[var(--color-text)] border border-white/10 rounded px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-primary)]"
          />
        </div>
      </div>
    </section>
  );
}
