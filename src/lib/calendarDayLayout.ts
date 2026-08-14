// Pure geometry for the mobile Calendar day view's hour-grid timeline — no
// DOM, unit-testable in the node environment, the same reason
// src/lib/paperImages.ts exists.
//
// Two minute-spaces meet here and must not be confused:
//
//   *wall minutes*   minutes since midnight, 0..1439. What an event's 'HH:MM'
//                    time means and the only thing the API ever speaks.
//   *offset minutes* minutes down the timeline from its top, 0..1440. The top
//                    is 4am, because the day view draws the app's 4am-anchored
//                    logical day (src/lib/dates.ts) rather than a calendar
//                    date — a 01:30 event belongs at the *bottom* of the night
//                    it was part of, not the top of the next morning.
//
// Everything below the conversion helpers works in offset minutes; conversion
// happens only at the edges, where an event is read from or written to the API.

import { DAY_ROLLOVER_HOUR } from './dates';

export const MINUTES_PER_DAY = 24 * 60;

/** Wall minutes at which the timeline starts (and therefore ends). */
export const DAY_START_MINUTES = DAY_ROLLOVER_HOUR * 60;

/** The hours labelling the grid, top to bottom: 4am … 11pm, then 12am … 3am. */
export const DISPLAY_HOURS = Array.from(
  { length: 24 },
  (_, i) => (DAY_ROLLOVER_HOUR + i) % 24
);

/** Where a wall-clock time sits on the timeline. */
export function offsetFromWallMinutes(wallMinutes: number): number {
  return (wallMinutes - DAY_START_MINUTES + MINUTES_PER_DAY) % MINUTES_PER_DAY;
}

/** The wall-clock time at a point on the timeline. The very bottom (1440) is
 * 4am again, which is what an event dragged flush to the end should store. */
export function wallMinutesFromOffset(offset: number): number {
  return (offset + DAY_START_MINUTES) % MINUTES_PER_DAY;
}

/** Whether an offset has passed midnight, i.e. lands on the calendar date
 * *after* the day key the timeline is drawn for. Everything from the 00:00 row
 * down does, since those wall times are earlier than the 4am start. */
export function offsetIsAfterMidnight(offset: number): boolean {
  return offset >= MINUTES_PER_DAY - DAY_START_MINUTES;
}

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

/** Width of an event's line. Deliberately the width of the mic button it
 * carries (Tailwind `w-7`): an event is a thin vertical stroke down the
 * timeline, not a block, so the rest of every row stays free for the thumb to
 * scroll on. */
export const EVENT_LINE_WIDTH_PX = 28;

/** Horizontal space between two overlapping events' lines. */
export const LANE_GUTTER_PX = 6;

/** Length of the grab zone at the bottom end of a line, used to drag its
 * length (== its duration). It lives entirely *inside* the line rather than
 * straddling its end, so a back-to-back event's line can never be covered by
 * the previous event's resize target. */
export const RESIZE_CAP_PX = 32;

/** No line is drawn shorter than this, whatever its duration — a line has to
 * be long enough to hold the resize cap and still leave something to grab for
 * a move. A 15-minute event therefore draws slightly long; the stored time
 * range is untouched. */
export const MIN_LINE_LENGTH_PX = RESIZE_CAP_PX + 12;

export function eventTopPx(startMinutes: number, pxPerMinute: number): number {
  return startMinutes * pxPerMinute;
}

export function eventLineLengthPx(
  startMinutes: number,
  endMinutes: number,
  pxPerMinute: number
): number {
  return Math.max(
    MIN_LINE_LENGTH_PX,
    Math.max(MIN_DURATION_MINUTES, endMinutes - startMinutes) * pxPerMinute
  );
}

export interface EventTimeRange {
  startMinutes: number;
  endMinutes: number;
}

/** Shift a whole event by a delta, clamped to the ends of the timeline (4am
 * and 4am). Duration is preserved — dragging the body of a block moves it, not
 * its length. */
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
 * and capped at the bottom of the timeline — dragging the bottom-edge handle. */
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

/** Left offset of an overlapping event's line: each depth gets its own lane
 * beside the previous one. Lines are thin, so overlaps sit side by side
 * instead of nesting — a nested inset only made sense while an event was a
 * full-width box. */
export function laneOffsetPx(depth: number): number {
  return depth * (EVENT_LINE_WIDTH_PX + LANE_GUTTER_PX);
}

export interface OverlapInput {
  id: string;
  startMinutes: number;
  endMinutes: number;
}

/**
 * Assigns each mutually-overlapping event a lane (0 = leftmost), so the day
 * view can place each overlapping line beside its siblings instead of on top
 * of them.
 *
 * Standard interval-graph greedy coloring: process longest events first (so
 * the longest event holds the leftmost lane and short ones stack out to its
 * right), and give each one the smallest lane not already occupied by an
 * overlapping event.
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
