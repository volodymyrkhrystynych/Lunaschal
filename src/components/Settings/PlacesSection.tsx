import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type SavedPlace } from '../../hooks/api';
import { currentPosition } from '../../lib/geo';

const inputClass =
  'rounded border border-white/20 bg-[var(--color-bg)] px-2 py-1 text-sm w-full';

export function PlacesSection() {
  const queryClient = useQueryClient();
  const places = useQuery({
    queryKey: ['savedPlaces'],
    queryFn: api.memory.places,
  });
  const [editing, setEditing] = useState<string>();
  const [name, setName] = useState('');
  const [notes, setNotes] = useState('');
  const [latitude, setLatitude] = useState('');
  const [longitude, setLongitude] = useState('');
  const [radius, setRadius] = useState('150');
  const [locating, setLocating] = useState(false);
  const [locationError, setLocationError] = useState('');
  function edit(place?: SavedPlace) {
    setEditing(place?.id);
    setName(place?.name ?? '');
    setNotes(place?.notes ?? '');
    setLatitude(place?.latitude == null ? '' : String(place.latitude));
    setLongitude(place?.longitude == null ? '' : String(place.longitude));
    setRadius(String(place?.radiusM ?? 150));
    setLocationError('');
  }
  const save = useMutation({
    mutationFn: () =>
      api.memory.savePlace(
        {
          name,
          notes,
          latitude: latitude.trim() ? Number(latitude) : null,
          longitude: longitude.trim() ? Number(longitude) : null,
          radiusM: Number(radius),
        },
        editing
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['savedPlaces'] });
      edit();
    },
  });
  const remove = useMutation({
    mutationFn: api.memory.deletePlace,
    onSuccess: (_, id) => {
      queryClient.invalidateQueries({ queryKey: ['savedPlaces'] });
      if (id === editing) edit();
    },
  });
  async function locate() {
    setLocating(true);
    const position = await currentPosition();
    setLocating(false);
    if (position) {
      setLatitude(String(position.latitude));
      setLongitude(String(position.longitude));
      setLocationError('');
    } else
      setLocationError(
        'Location unavailable. You can enter coordinates or leave them empty.'
      );
  }
  return (
    <div className="space-y-3">
      <p className="text-sm text-[var(--color-text-muted)]">
        Save Home, Work and other places so chat and the overnight briefing can
        understand your locations. Add an address or aliases in Notes. Optional
        coordinates match locations recorded with journal entries and meals.
      </p>
      {places.isError && <p role="alert">Could not load saved places.</p>}
      {places.data?.map(place => (
        <div key={place.id} className="flex items-center gap-3 text-sm">
          <span className="flex-1">
            {place.name}
            {place.notes ? ` — ${place.notes}` : ''}
          </span>
          <button onClick={() => edit(place)}>Edit {place.name}</button>
          <button
            disabled={remove.isPending}
            onClick={() => remove.mutate(place.id)}
          >
            Delete {place.name}
          </button>
        </div>
      ))}
      <form
        className="space-y-2"
        onSubmit={e => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <label className="block text-sm">
          Place name
          <input
            required
            maxLength={120}
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="Home, Work, Brother’s house…"
            className={inputClass}
          />
        </label>
        <label className="block text-sm">
          Notes
          <textarea
            maxLength={2000}
            value={notes}
            onChange={e => setNotes(e.target.value)}
            className={inputClass}
          />
        </label>
        <div className="flex gap-2">
          <label className="text-sm flex-1">
            Latitude
            <input
              type="number"
              min={-90}
              max={90}
              step="any"
              value={latitude}
              onChange={e => setLatitude(e.target.value)}
              className={inputClass}
            />
          </label>
          <label className="text-sm flex-1">
            Longitude
            <input
              type="number"
              min={-180}
              max={180}
              step="any"
              value={longitude}
              onChange={e => setLongitude(e.target.value)}
              className={inputClass}
            />
          </label>
        </div>
        <label className="block text-sm">
          Matching radius (metres)
          <input
            type="number"
            min={10}
            max={5000}
            required
            value={radius}
            onChange={e => setRadius(e.target.value)}
            className={inputClass}
          />
        </label>
        <div className="flex flex-wrap gap-3 text-sm">
          <button type="button" disabled={locating} onClick={locate}>
            {locating ? 'Locating…' : 'Use current location'}
          </button>
          <button
            type="submit"
            disabled={save.isPending}
            className="rounded bg-[var(--color-primary)] px-3 py-1 text-white"
          >
            {editing ? 'Save place' : 'Add place'}
          </button>
          {editing && (
            <button type="button" onClick={() => edit()}>
              Cancel edit
            </button>
          )}
        </div>
      </form>
      {(save.error || remove.error || locationError) && (
        <p role="alert" className="text-sm text-red-400">
          {save.error?.message || remove.error?.message || locationError}
        </p>
      )}
    </div>
  );
}
