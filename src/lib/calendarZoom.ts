// How tall an hour is drawn in the Calendar day view, and where that choice is
// remembered.
//
// Split out of calendarDayLayout.ts on purpose: that module is pure geometry
// with no DOM, so it can be unit-tested in the node environment. This one
// touches localStorage, and the clamp below is the pure half that the geometry
// callers actually need.
//
// The scale is the whole point of the control. At 1x an hour is 60px, so a
// five-minute snap step is five pixels — near the tap slop of a finger and
// well under the width of a mouse cursor's hot zone, which is what made
// precise times so hard to land. 2x and 3x buy that precision back at the cost
// of scrolling further to cross a day, and which trade is right depends on the
// screen, so it is the user's to make rather than a constant here.

const STORAGE_KEY = 'lunaschal:calendarDayZoom';

/** Selectable zoom levels, in px per minute. 1 = the original 60px hour. */
export const DAY_ZOOM_LEVELS = [1, 2, 3] as const;

export type DayZoom = (typeof DAY_ZOOM_LEVELS)[number];

/** 2x by default: the level that makes a five-minute step a ten-pixel one
 * without halving how much of the day is on screen at once. */
export const DEFAULT_DAY_ZOOM: DayZoom = 2;

export function isDayZoom(value: unknown): value is DayZoom {
  return (DAY_ZOOM_LEVELS as readonly number[]).includes(value as number);
}

/** The level `steps` away from `zoom`, stopping at either end rather than
 * wrapping — a zoom control that jumps from 3x back to 1x on one more tap is
 * a control you stop trusting. */
export function stepZoom(zoom: DayZoom, steps: number): DayZoom {
  const index = DAY_ZOOM_LEVELS.indexOf(zoom);
  const next = Math.max(
    0,
    Math.min(DAY_ZOOM_LEVELS.length - 1, (index < 0 ? 0 : index) + steps)
  );
  return DAY_ZOOM_LEVELS[next];
}

export function getStoredZoom(): DayZoom {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = raw === null ? NaN : Number(raw);
    return isDayZoom(parsed) ? parsed : DEFAULT_DAY_ZOOM;
  } catch {
    // Private mode, or storage disabled. The default is a working day view.
    return DEFAULT_DAY_ZOOM;
  }
}

export function storeZoom(zoom: DayZoom): void {
  try {
    localStorage.setItem(STORAGE_KEY, String(zoom));
  } catch {
    // Not worth failing a zoom over.
  }
}
