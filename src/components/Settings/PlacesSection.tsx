import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  api,
  type PlaceRole,
  type PlaceRolePeriod,
  type SavedPlace,
} from '../../hooks/api';
import { localDayKey } from '../../lib/dates';
import { currentPosition } from '../../lib/geo';
import {
  historyByRole,
  patternsSummary,
  periodRange,
  roleLabel,
  roleOf,
} from '../../lib/placeRoles';

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
  const periods = useQuery({
    queryKey: ['placeRoles'],
    queryFn: api.memory.placeRoles,
  });
  const today = localDayKey();
  // A Home change re-measures every job's commute, and every role change can
  // redraw a day's map, so both are refetched rather than patched in place.
  const rolesChanged = () => {
    queryClient.invalidateQueries({ queryKey: ['placeRoles'] });
    queryClient.invalidateQueries({ queryKey: ['jobs'] });
    queryClient.invalidateQueries({ queryKey: ['location', 'journal'] });
  };
  const [rolePlace, setRolePlace] = useState('');
  const [role, setRole] = useState<PlaceRole>('home');
  const [since, setSince] = useState(today);
  const assign = useMutation({
    mutationFn: () => api.memory.assignRole(role, rolePlace, since),
    onSuccess: rolesChanged,
  });
  const updateRole = useMutation({
    mutationFn: ({
      id,
      ...changes
    }: {
      id: string;
      startedOn?: string;
      endedOn?: string | null;
    }) => api.memory.updateRole(id, changes),
    onSuccess: rolesChanged,
  });
  const deleteRole = useMutation({
    mutationFn: api.memory.deleteRole,
    onSuccess: rolesChanged,
  });
  const located = (places.data ?? []).filter(
    p => p.latitude != null && p.longitude != null
  );
  const history = historyByRole(periods.data ?? []);
  const patterns = useQuery({
    queryKey: ['location', 'patterns'],
    queryFn: () => api.location.patterns(),
  });
  const patternLine = patternsSummary(patterns.data);
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
        Save the places you go so chat and the overnight briefing can understand
        your locations. Add an address or aliases in Notes. Optional coordinates
        match locations recorded with journal entries and meals. Make a place
        your Home or a Work below; the location trail, the briefing, weather and
        job commutes use whichever applied on the day.
      </p>
      {places.isError && <p role="alert">Could not load saved places.</p>}
      {places.data?.map(place => {
        const current = roleOf(place.id, periods.data ?? [], today);
        return (
          <div key={place.id} className="flex items-center gap-3 text-sm">
            <span className="flex-1">
              {place.name}
              {current && (
                <span className="ml-2 rounded bg-white/10 px-1.5 py-0.5 text-xs">
                  {roleLabel(current.role)} {periodRange(current)}
                </span>
              )}
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
        );
      })}
      {located.length > 0 && (
        <form
          aria-label="Set Home or Work"
          className="flex flex-wrap items-end gap-2 text-sm"
          onSubmit={e => {
            e.preventDefault();
            if (rolePlace) assign.mutate();
          }}
        >
          <label className="flex-1 min-w-[8rem]">
            Place
            <select
              required
              value={rolePlace}
              onChange={e => setRolePlace(e.target.value)}
              className={inputClass}
            >
              <option value="">Choose…</option>
              {located.map(p => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Role
            <select
              value={role}
              onChange={e => setRole(e.target.value as PlaceRole)}
              className={inputClass}
            >
              <option value="home">Home</option>
              <option value="work">Work</option>
            </select>
          </label>
          <label>
            Since
            <input
              type="date"
              required
              value={since}
              onChange={e => setSince(e.target.value)}
              className={inputClass}
            />
          </label>
          <button
            type="submit"
            disabled={assign.isPending || !rolePlace}
            className="rounded bg-[var(--color-primary)] px-3 py-1 text-white"
          >
            {role === 'home' ? 'Set as Home' : 'Add as Work'}
          </button>
        </form>
      )}
      <p className="text-xs text-[var(--color-text-muted)]">
        Moving? Add the new place and set it as Home from the move date; the old
        one stays in the history. Editing a place’s coordinates corrects it for
        all of its history. Only places with coordinates can be Home or Work.
      </p>
      {(['home', 'work'] as const).map(
        r =>
          history[r].length > 0 && (
            <section key={r} aria-label={`${roleLabel(r)} history`}>
              <h4 className="text-sm font-medium">{roleLabel(r)} history</h4>
              <ul className="space-y-1">
                {history[r].map(period => (
                  <RolePeriodRow
                    key={period.id}
                    period={period}
                    busy={updateRole.isPending || deleteRole.isPending}
                    onChange={changes =>
                      updateRole.mutate({ id: period.id, ...changes })
                    }
                    onDelete={() => deleteRole.mutate(period.id)}
                  />
                ))}
              </ul>
            </section>
          )
      )}
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
      {patternLine && (
        <p
          className="text-sm text-[var(--color-text-muted)]"
          aria-label="Location patterns"
        >
          {patternLine}
        </p>
      )}
      {(save.error ||
        remove.error ||
        assign.error ||
        updateRole.error ||
        deleteRole.error ||
        locationError) && (
        <p role="alert" className="text-sm text-red-400">
          {save.error?.message ||
            remove.error?.message ||
            assign.error?.message ||
            updateRole.error?.message ||
            deleteRole.error?.message ||
            locationError}
        </p>
      )}
    </div>
  );
}

/** One period in a role's history. Dates commit on change; clearing the end
 *  date reopens the period. */
function RolePeriodRow({
  period,
  busy,
  onChange,
  onDelete,
}: {
  period: PlaceRolePeriod;
  busy: boolean;
  onChange: (changes: { startedOn?: string; endedOn?: string | null }) => void;
  onDelete: () => void;
}) {
  return (
    <li className="flex flex-wrap items-center gap-2 text-sm">
      <span className="flex-1 min-w-[6rem]">{period.placeName}</span>
      <label className="text-xs">
        From
        <input
          type="date"
          aria-label={`${period.placeName} from`}
          defaultValue={period.startedOn}
          onBlur={e => {
            if (e.target.value && e.target.value !== period.startedOn)
              onChange({ startedOn: e.target.value });
          }}
          className={inputClass}
        />
      </label>
      <label className="text-xs">
        Until
        <input
          type="date"
          aria-label={`${period.placeName} until`}
          defaultValue={period.endedOn ?? ''}
          onBlur={e => {
            const next = e.target.value || null;
            if (next !== period.endedOn) onChange({ endedOn: next });
          }}
          className={inputClass}
        />
      </label>
      <button type="button" disabled={busy} onClick={onDelete}>
        Remove {period.placeName} period
      </button>
    </li>
  );
}
