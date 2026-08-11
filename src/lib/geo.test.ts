import { describe, it, expect, vi } from 'vitest';
import { currentPosition } from './geo';

const geo = (impl: Partial<Geolocation>) => impl as Geolocation;

describe('currentPosition', () => {
  it('resolves the coordinates', async () => {
    const g = geo({
      getCurrentPosition: (ok: PositionCallback) =>
        ok({
          coords: { latitude: 43.64, longitude: -79.39 },
        } as GeolocationPosition),
    });
    expect(await currentPosition(g)).toEqual({
      latitude: 43.64,
      longitude: -79.39,
    });
  });

  it('resolves null when the user denies it, rather than rejecting', async () => {
    // Never rejects, so callers can await it without a try/catch — a refused
    // permission costs the coordinates and nothing else.
    const g = geo({
      getCurrentPosition: (
        _ok: PositionCallback,
        fail?: PositionErrorCallback
      ) => fail?.({ code: 1, message: 'denied' } as GeolocationPositionError),
    });
    await expect(currentPosition(g)).resolves.toBeNull();
  });

  it('resolves null when there is no geolocation at all', async () => {
    await expect(currentPosition(undefined)).resolves.toBeNull();
  });

  it('asks for a cheap, cached fix', async () => {
    // "Which restaurant" needs no GPS lock; high accuracy would cost seconds and
    // battery for precision nothing reads back.
    const getCurrentPosition = vi.fn();
    await Promise.race([
      currentPosition(geo({ getCurrentPosition })),
      Promise.resolve(),
    ]);
    const opts = getCurrentPosition.mock.calls[0][2];
    expect(opts.enableHighAccuracy).toBe(false);
    expect(opts.timeout).toBeGreaterThan(0);
    expect(opts.maximumAge).toBeGreaterThan(0);
  });
});
