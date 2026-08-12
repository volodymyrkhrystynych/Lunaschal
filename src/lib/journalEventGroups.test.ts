import { describe, it, expect } from 'vitest';
import { computeEventGroupSpans } from './journalEventGroups';
import type { FeedItem } from './journalFeed';
import type { CalendarEvent, JournalEntry } from '../hooks/api';

function entryItem(id: string, createdAt: string, entryIndex = 0): FeedItem {
  const entry: JournalEntry = {
    id,
    createdAt,
    content: `entry ${id}`,
    rawContent: null,
    title: null,
    tags: null,
    curatedTags: [],
    updatedAt: createdAt,
  };
  return { kind: 'entry', entry, entryIndex };
}

function calendarEvent(
  id: string,
  overrides: Partial<CalendarEvent> = {}
): CalendarEvent {
  return {
    id,
    title: `Event ${id}`,
    description: 'Went for a walk.',
    date: '2026-07-08',
    time: '09:00',
    endTime: '10:00',
    allDay: false,
    tags: null,
    journalId: null,
    createdAt: '2026-07-08T09:00:00',
    repeatFreq: null,
    repeatInterval: null,
    repeatByweekday: null,
    repeatUntil: null,
    categoryTags: JSON.stringify(['outside']),
    classifiedAt: '2026-07-08T10:05:00',
    classificationError: null,
    ...overrides,
  };
}

describe('computeEventGroupSpans', () => {
  it('wraps a single entry that falls inside the event window', () => {
    const feed = [entryItem('e1', '2026-07-08T09:30:00')];
    const spans = computeEventGroupSpans(feed, [calendarEvent('ev1')]);
    expect(spans).toEqual([
      {
        event: expect.objectContaining({ id: 'ev1' }),
        startIndex: 0,
        endIndex: 0,
      },
    ]);
  });

  it('wraps a contiguous run of several entries', () => {
    const feed = [
      entryItem('before', '2026-07-08T11:00:00'), // after the window (newer)
      entryItem('e1', '2026-07-08T09:50:00'),
      entryItem('e2', '2026-07-08T09:20:00'),
      entryItem('e3', '2026-07-08T09:05:00'),
      entryItem('after', '2026-07-08T08:00:00'), // before the window (older)
    ];
    const spans = computeEventGroupSpans(feed, [calendarEvent('ev1')]);
    expect(spans).toEqual([
      {
        event: expect.objectContaining({ id: 'ev1' }),
        startIndex: 1,
        endIndex: 3,
      },
    ]);
  });

  it('produces no span when nothing falls in the window', () => {
    const feed = [entryItem('e1', '2026-07-08T14:00:00')];
    const spans = computeEventGroupSpans(feed, [calendarEvent('ev1')]);
    expect(spans).toEqual([]);
  });

  it('skips an event with no assigned categories, even with entries in range', () => {
    const feed = [entryItem('e1', '2026-07-08T09:30:00')];
    const spans = computeEventGroupSpans(feed, [
      calendarEvent('ev1', { categoryTags: null, classifiedAt: null }),
    ]);
    expect(spans).toEqual([]);
  });

  it('treats an all-day event as covering the whole local day', () => {
    const feed = [entryItem('e1', '2026-07-08T23:00:00')];
    const spans = computeEventGroupSpans(feed, [
      calendarEvent('ev1', { allDay: true, time: null, endTime: null }),
    ]);
    expect(spans).toHaveLength(1);
  });

  it('defaults a missing endTime to a 30-minute window', () => {
    const inWindow = entryItem('in', '2026-07-08T09:15:00');
    const outOfWindow = entryItem('out', '2026-07-08T09:45:00');
    const spans = computeEventGroupSpans(
      [inWindow, outOfWindow],
      [calendarEvent('ev1', { endTime: null })]
    );
    expect(spans).toEqual([
      {
        event: expect.objectContaining({ id: 'ev1' }),
        startIndex: 0,
        endIndex: 0,
      },
    ]);
  });

  it('uses occurrenceDate over the series anchor date for a recurring event', () => {
    const feed = [entryItem('e1', '2026-07-15T09:30:00')];
    const spans = computeEventGroupSpans(feed, [
      calendarEvent('ev1', {
        date: '2026-07-08',
        occurrenceDate: '2026-07-15',
        isRecurring: true,
      }),
    ]);
    expect(spans).toHaveLength(1);
  });

  it('sorts multiple spans by their position in the feed', () => {
    const feed = [
      entryItem('e1', '2026-07-08T09:30:00'),
      entryItem('gap', '2026-07-08T08:00:00'),
      entryItem('e2', '2026-07-08T07:30:00'),
    ];
    const events = [
      calendarEvent('later', {
        date: '2026-07-08',
        time: '07:00',
        endTime: '07:45',
      }),
      calendarEvent('earlier', {
        date: '2026-07-08',
        time: '09:00',
        endTime: '10:00',
      }),
    ];
    const spans = computeEventGroupSpans(feed, events);
    expect(spans.map(s => s.event.id)).toEqual(['earlier', 'later']);
  });
});
