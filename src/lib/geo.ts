/**
 * Reading the device's position, for the two places that log something physical:
 * the Food tab's capture box and a photo attached in Chat.
 *
 * Extracted from FoodCapture so both use one set of options. The options are the
 * point: this is a best-effort convenience, never a gate. It resolves `null`
 * rather than rejecting on denial, unavailability or timeout, so a caller can
 * always `await` it without a try/catch and a refused permission costs the user
 * nothing but the coordinates.
 */
export interface Coords {
  latitude: number;
  longitude: number;
}

/** How long to wait before giving up and saving without a location. */
const TIMEOUT_MS = 8000;

/** A cached fix up to a minute old is fine — you have not moved far, and it
 *  avoids a fresh GPS acquisition every time a photo is attached. */
const MAX_AGE_MS = 60000;

export function currentPosition(
  geolocation: Geolocation | undefined = typeof navigator === 'undefined'
    ? undefined
    : navigator.geolocation
): Promise<Coords | null> {
  return new Promise(resolve => {
    if (!geolocation) return resolve(null);
    geolocation.getCurrentPosition(
      pos =>
        resolve({
          latitude: pos.coords.latitude,
          longitude: pos.coords.longitude,
        }),
      () => resolve(null),
      {
        // Low accuracy on purpose: "which restaurant" does not need a GPS lock,
        // and high accuracy costs seconds and battery for precision nobody reads.
        enableHighAccuracy: false,
        timeout: TIMEOUT_MS,
        maximumAge: MAX_AGE_MS,
      }
    );
  });
}
