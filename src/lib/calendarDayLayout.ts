// Pure geometry for the mobile Calendar day view's hour-grid timeline — no
// DOM, unit-testable in the node environment, the same reason
// src/lib/paperImages.ts exists. Time is tracked as minutes-since-midnight
// throughout; conversion to/from the 'HH:MM' strings the API speaks happens
// only at the edges (timeToMinutes/minutesToTime).

export const MINUTES_PER_DAY = 24 * 60;

/** Nothing may be resized shorter than this. A zero-duration event is
 * unselectable and effectively unrecoverable — same reasoning as
 * paperImages.ts's MIN_IMAGE_SIZE. */
export const MIN_DURATION_MINUTES = 15;

/** Drag/resize snaps to this grid, so a touch drag lands on a sane time
 * instead of an arbitrary pixel offset. */
export const SNAP_MINUTES = 5;

/** Length a newly-created event (or one with a start but no saved end) gets
 * by default. Shared by DayView's "+" button and journalEventGroups.ts's
 * window computation, so the two agree on how long an untimed-end event
 * covers. */
export const DEFAULT_EVENT_DURATION_MINUTES = 30;

export function timeToMinutes(time: string): number {
  const [h, m] = time.slice(0, 5).split(':').map(Number);
  return (h || 0) * 60 + (m || 0);
}

export function minutesToTime(minutes: number): string {
  const clamped = Math.max(
    0,
    Math.min(MINUTES_PER_DAY - 1, Math.round(minutes))
  );
  const h = Math.floor(clamped / 60);
  const m = clamped % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

export function snapMinutes(
  minutes: number,
  step: number = SNAP_MINUTES
): number {
  return Math.round(minutes / step) * step;
}

export function eventTopPx(startMinutes: number, pxPerMinute: number): number {
  return startMinutes * pxPerMinute;
}

export function eventHeightPx(
  startMinutes: number,
  endMinutes: number,
  pxPerMinute: number
): number {
  return (
    Math.max(MIN_DURATION_MINUTES, endMinutes - startMinutes) * pxPerMinute
  );
}

export interface EventTimeRange {
  startMinutes: number;
  endMinutes: number;
}

/** Shift a whole event by a delta, clamped to the day's bounds. Duration is
 * preserved — dragging the body of a block moves it, not its length. */
export function moveEventByMinutes(
  range: EventTimeRange,
  deltaMinutes: number
): EventTimeRange {
  const duration = range.endMinutes - range.startMinutes;
  const start = Math.max(
    0,
    Math.min(range.startMinutes + deltaMinutes, MINUTES_PER_DAY - duration)
  );
  return { startMinutes: start, endMinutes: start + duration };
}

/** Extend or shrink an event from its end, floored at MIN_DURATION_MINUTES
 * and capped at midnight — dragging the bottom-edge handle. */
export function resizeEventEndByMinutes(
  range: EventTimeRange,
  deltaMinutes: number
): EventTimeRange {
  const end = Math.max(
    range.startMinutes + MIN_DURATION_MINUTES,
    Math.min(MINUTES_PER_DAY, range.endMinutes + deltaMinutes)
  );
  return { startMinutes: range.startMinutes, endMinutes: end };
}

/** Pixels of pointer travel -> a snapped minute delta, for a drag handler to
 * feed into moveEventByMinutes/resizeEventEndByMinutes. */
export function pxDeltaToMinutes(
  pxDelta: number,
  pxPerMinute: number,
  snap: number = SNAP_MINUTES
): number {
  return snapMinutes(pxDelta / pxPerMinute, snap);
}

/** Horizontal inset (from each side) per nesting depth, and how many px of
 * z-stacking distinguishes each layer — depth 0 (outermost) gets no inset. */
export const OVERLAP_INSET_PX = 10;

export interface OverlapInput {
  id: string;
  startMinutes: number;
  endMinutes: number;
}

/**
 * Assigns each mutually-overlapping event a nesting depth (0 = outermost),
 * so the day view can inset each subsequent overlapping block from its
 * siblings and keep every border visible (picture-in-picture, not
 * side-by-side columns).
 *
 * Standard interval-graph greedy coloring: process longest events first (so
 * a long event stays the outer frame around a short one nested inside it,
 * matching the confirmed mockup), and give each one the smallest depth not
 * already occupied by an overlapping event.
 */
export function computeOverlapDepth(
  events: OverlapInput[]
): Map<string, number> {
  const sorted = [...events].sort((a, b) => {
    const durA = a.endMinutes - a.startMinutes;
    const durB = b.endMinutes - b.startMinutes;
    return (
      durB - durA || a.startMinutes - b.startMinutes || a.id.localeCompare(b.id)
    );
  });

  const depths = new Map<string, number>();
  const placedByDepth: EventTimeRange[][] = [];

  for (const ev of sorted) {
    let depth = 0;
    while (
      placedByDepth[depth]?.some(
        p => p.startMinutes < ev.endMinutes && ev.startMinutes < p.endMinutes
      )
    ) {
      depth++;
    }
    if (!placedByDepth[depth]) placedByDepth[depth] = [];
    placedByDepth[depth].push({
      startMinutes: ev.startMinutes,
      endMinutes: ev.endMinutes,
    });
    depths.set(ev.id, depth);
  }

  return depths;
}
