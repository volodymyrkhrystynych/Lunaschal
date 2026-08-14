// Pure grouping pass for the Journal feed: which calendar events' time
// windows cover a run of the already-built, time-descending FeedItem[]
// (src/lib/journalFeed.ts). Deliberately NOT a change to buildFeed's output
// shape — entryIndex on each FeedItem is load-bearing for keyboard
// navigation, so this stays a read-only overlay computed after the fact and
// consumed at render time to open/close a wrapping border around a span of
// items, rather than restructuring the array itself.

import type { CalendarEvent } from '../hooks/api';
import type { FeedItem } from './journalFeed';
import {
  DEFAULT_EVENT_DURATION_MINUTES,
  minutesToTime,
  timeToMinutes,
} from './calendarDayLayout';
import { parseCategoryTags } from './calendarCategories';
import { DAY_ROLLOVER_HOUR } from './dates';

const ROLLOVER_TIME = `${String(DAY_ROLLOVER_HOUR).padStart(2, '0')}:00:00`;
const DAY_MS = 24 * 60 * 60 * 1000;

export interface EventGroupSpan {
  event: CalendarEvent;
  /** Inclusive index range into the `feed` array passed to
   * computeEventGroupSpans — the wrapper opens before `startIndex` and
   * closes after `endIndex`. */
  startIndex: number;
  endIndex: number;
}

function feedItemTimeMs(item: FeedItem): number {
  switch (item.kind) {
    case 'entry':
      return new Date(item.entry.createdAt).getTime();
    case 'transcription':
      return new Date(item.transcription.createdAt).getTime();
    case 'conversation':
      return new Date(item.conversation.updatedAt).getTime();
    case 'paper':
      return new Date(item.paper.archivedAt).getTime();
    case 'food':
      return new Date(item.food.createdAt).getTime();
    case 'taskEvent':
      return new Date(item.taskEvent.createdAt).getTime();
  }
}

/** [startMs, endMs] the event covers in local time, or null when it has no
 * defined window (untimed, non-all-day — nothing to group by). Recurring
 * events use occurrenceDate, the actual instance date, over the series'
 * anchor `date`. An all-day event's window is the app's 4am-anchored logical
 * day (src/lib/dates.ts), not literal midnight-to-midnight, so a journal
 * entry written at 1am the next morning still groups with the day before. */
function eventWindowMs(event: CalendarEvent): [number, number] | null {
  const dateStr = event.occurrenceDate ?? event.date;
  if (event.allDay) {
    const start = new Date(`${dateStr}T${ROLLOVER_TIME}`).getTime();
    return [start, start + DAY_MS - 1000];
  }
  if (!event.time) return null;
  const start = new Date(`${dateStr}T${event.time}`).getTime();
  const endTime =
    event.endTime ??
    minutesToTime(timeToMinutes(event.time) + DEFAULT_EVENT_DURATION_MINUTES);
  const end = new Date(`${dateStr}T${endTime}`).getTime();
  return [start, end];
}

/**
 * For each categorized event whose window covers at least one feed item,
 * finds the contiguous index range those items occupy in `feed`.
 *
 * Because `feed` is already time-sorted and an event's window is a
 * contiguous time range, every matching item is contiguous in the array too
 * — so this is a single linear scan per event, not a search. Only events
 * with at least one AI-assigned category produce a span: an event that
 * hasn't been transcribed/tagged yet has no border color to render and stays
 * out of the Journal feed, per the day view being where it's still "in
 * progress".
 */
export function computeEventGroupSpans(
  feed: FeedItem[],
  events: CalendarEvent[]
): EventGroupSpan[] {
  const spans: EventGroupSpan[] = [];

  for (const event of events) {
    if (parseCategoryTags(event.categoryTags).length === 0) continue;
    const window = eventWindowMs(event);
    if (!window) continue;
    const [start, end] = window;

    let startIndex = -1;
    let endIndex = -1;
    for (let i = 0; i < feed.length; i++) {
      const t = feedItemTimeMs(feed[i]);
      if (t >= start && t <= end) {
        if (startIndex === -1) startIndex = i;
        endIndex = i;
      } else if (startIndex !== -1) {
        // The feed is time-descending and the window is contiguous: once
        // we've entered it and then left, nothing further can re-enter.
        break;
      }
    }

    if (startIndex !== -1) spans.push({ event, startIndex, endIndex });
  }

  return spans.sort((a, b) => a.startIndex - b.startIndex);
}
