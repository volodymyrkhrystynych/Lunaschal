import { useState } from 'react';
import { currentPosition, formatCoords, type Coords } from '../lib/geo';

/**
 * Asking for a location, explicitly.
 *
 * The Food tab and the Chat composer both grab the device fix silently on
 * submit; the Journal does not, and the difference is deliberate. A journal
 * entry is usually a photograph, and **a photograph taken through the browser's
 * camera arrives with its GPS EXIF stripped** — iOS only hands the original
 * metadata over for a picture picked out of the library. So for the case that
 * matters most the entry's own fix is the only location that will ever exist,
 * and a silent best-effort grab is the wrong shape for something load-bearing:
 * a refused permission would be indistinguishable from a photo that simply had
 * no EXIF, and nobody would find out until they went looking for where they
 * were a year later.
 *
 * Hence a control with a state you can read before you save. Nothing is
 * requested until it is pressed — no prompt appears merely from opening the
 * composer — and saving never waits on it: `currentPosition` resolves null on
 * denial, unavailability or timeout rather than rejecting, so the worst case is
 * an entry with no location, exactly as today.
 */
export type LocationState = 'idle' | 'locating' | 'ready' | 'unavailable';

export interface LocationAsk {
  coords: Coords | null;
  state: LocationState;
  /** Ask the device, or clear a fix already held. */
  toggle: () => void;
  /** Drop the fix and go back to `idle` — for a composer reset after send. */
  reset: () => void;
}

export function useLocationAsk(): LocationAsk {
  const [coords, setCoords] = useState<Coords | null>(null);
  const [state, setState] = useState<LocationState>('idle');

  const toggle = () => {
    // A second press on a held fix clears it: the button is the only way to
    // take back a location that was attached by accident, and an entry that is
    // *not* located has to stay reachable without reloading the composer.
    if (coords) {
      setCoords(null);
      setState('idle');
      return;
    }
    if (state === 'locating') return;
    setState('locating');
    void currentPosition().then(pos => {
      setCoords(pos);
      // 'unavailable' rather than back to 'idle': the press happened and
      // produced nothing, and the user should see that rather than a button
      // that looks untouched. Pressing again retries.
      setState(pos ? 'ready' : 'unavailable');
    });
  };

  const reset = () => {
    setCoords(null);
    setState('idle');
  };

  return { coords, state, toggle, reset };
}

const LABELS: Record<LocationState, string> = {
  idle: 'Add location',
  locating: 'Locating…',
  ready: 'Location added',
  unavailable: 'Location unavailable',
};

export function LocationButton({
  ask,
  testId = 'location',
}: {
  ask: LocationAsk;
  testId?: string;
}) {
  const { coords, state } = ask;
  return (
    <button
      type="button"
      onClick={ask.toggle}
      disabled={state === 'locating'}
      data-testid={`${testId}-button`}
      title={
        coords
          ? `${formatCoords(coords)} — click to remove`
          : 'Attach where you are now'
      }
      aria-label={LABELS[state]}
      className={
        'px-2 py-1 text-sm rounded border transition-colors disabled:opacity-60 ' +
        (state === 'ready'
          ? 'border-[var(--color-accent)] text-[var(--color-accent)]'
          : 'border-white/10 text-[var(--color-text-muted)] hover:text-[var(--color-text)]')
      }
    >
      📍{' '}
      <span data-testid={`${testId}-label`}>
        {state === 'ready' && coords ? formatCoords(coords) : LABELS[state]}
      </span>
    </button>
  );
}
