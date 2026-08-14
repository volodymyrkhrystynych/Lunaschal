import { useEffect, useMemo, useRef } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type CalendarEvent } from '../../hooks/api';
import {
  DAY_START_MINUTES,
  DEFAULT_EVENT_DURATION_MINUTES,
  DISPLAY_HOURS,
  MIN_DURATION_MINUTES,
  MINUTES_PER_DAY,
  computeOverlapDepth,
  minutesToTime,
  offsetFromWallMinutes,
  offsetIsAfterMidnight,
  timeToMinutes,
  wallMinutesFromOffset,
  type EventTimeRange,
} from '@/lib/calendarDayLayout';
import { addDays, localDayKey } from '@/lib/dates';
import { sleepBands } from '@/lib/sleep';
import { DayEventLayer, type LaidOutEvent } from './DayEventLayer';

/** 60px-tall hour rows. */
const PX_PER_MINUTE = 1;

/** Where the "+" button drops a new event on a day that isn't today, and where
 * such a day is scrolled to: 8am, a plausible start to a day. */
const DEFAULT_HOUR = 8;

/** An event is drawn on the day whose 4am-to-4am window contains it, so the
 * timeline spans two calendar dates: `date` from 4am, then the date after it
 * up to 4am. This splits a fetched list into the part that belongs here. */
function belongsToDay(event: CalendarEvent, isNextDate: boolean): boolean {
  if (event.allDay || !event.time) return false;
  const wall = timeToMinutes(event.time);
  return isNextDate ? wall < DAY_START_MINUTES : wall >= DAY_START_MINUTES;
}

/** Poll only while something is transcribed but not yet classified, so the
 * category border appears as soon as the background job finishes without
 * needing a dedicated SSE stream for one small field. */
function classificationPoll(list: CalendarEvent[] | undefined) {
  return list?.some(e => e.description && !e.classifiedAt) ? 2000 : false;
}

interface TimelineEvent extends LaidOutEvent {
  /** The calendar date the row is actually stored under — `date` for most of
   * the timeline, the date after it below midnight. Needed to tell a drag that
   * merely retimed an event from one that moved it across the date line. */
  sourceDate: string;
}

interface DayViewProps {
  date: string;
  onOpenEvent: (event: CalendarEvent) => void;
  onEditSleep: () => void;
}

export function DayView({ date, onOpenEvent, onEditSleep }: DayViewProps) {
  const queryClient = useQueryClient();
  const scrollRef = useRef<HTMLDivElement>(null);
  const scrolledRef = useRef(false);

  const nextDate = addDays(date, 1);

  const { data: events } = useQuery({
    queryKey: ['calendar', 'date', date],
    queryFn: () => api.calendar.listByDate(date),
    refetchInterval: query =>
      classificationPoll(query.state.data as CalendarEvent[] | undefined),
  });

  // The tail of the timeline lives on the next calendar date. Fetched
  // separately rather than by range so both halves share the per-date cache
  // key the rest of the Calendar already invalidates.
  const { data: nextEvents } = useQuery({
    queryKey: ['calendar', 'date', nextDate],
    queryFn: () => api.calendar.listByDate(nextDate),
    refetchInterval: query =>
      classificationPoll(query.state.data as CalendarEvent[] | undefined),
  });

  const { data: sleep } = useQuery({
    queryKey: ['calendar', 'sleep', date],
    queryFn: () => api.calendar.sleep.get(date),
  });

  const bands = useMemo(
    () => (sleep ? sleepBands(sleep, date) : []),
    [sleep, date]
  );

  const timed = useMemo(
    () => [
      ...(events || [])
        .filter(e => belongsToDay(e, false))
        .map(event => ({ event, sourceDate: date })),
      ...(nextEvents || [])
        .filter(e => belongsToDay(e, true))
        .map(event => ({ event, sourceDate: nextDate })),
    ],
    [events, nextEvents, date, nextDate]
  );

  // All-day chips come from this day key alone: an all-day event's window is
  // already the 4am day of the date it carries (see src/lib/journalEventGroups).
  const allDay = useMemo(() => (events || []).filter(e => e.allDay), [events]);

  const laidOut: TimelineEvent[] = useMemo(() => {
    const withRanges = timed.map(({ event, sourceDate }) => {
      const wallStart = timeToMinutes(event.time!);
      const startMinutes = offsetFromWallMinutes(wallStart);
      // Length is carried as a duration rather than a second offset: an event
      // ending at 00:30 has an end offset smaller than its start, and only the
      // duration survives the wrap intact.
      const duration = event.endTime
        ? timeToMinutes(event.endTime) - wallStart
        : DEFAULT_EVENT_DURATION_MINUTES;
      return {
        event,
        sourceDate,
        range: { startMinutes, endMinutes: startMinutes + duration },
      };
    });
    const depths = computeOverlapDepth(
      withRanges.map(w => ({ id: w.event.id, ...w.range }))
    );
    return withRanges.map(w => ({ ...w, depth: depths.get(w.event.id) ?? 0 }));
  }, [timed]);

  // Re-arm the auto-scroll-to-now on every date change, so paging to a new
  // day scrolls to something sensible instead of wherever the last day left
  // the scroll position.
  useEffect(() => {
    scrolledRef.current = false;
  }, [date]);

  useEffect(() => {
    if (scrolledRef.current || !scrollRef.current) return;
    const now = new Date();
    const isToday = date === localDayKey(now);
    const targetOffset = offsetFromWallMinutes(
      isToday ? now.getHours() * 60 : DEFAULT_HOUR * 60
    );
    scrollRef.current.scrollTop = Math.max(
      0,
      targetOffset * PX_PER_MINUTE - 120
    );
    scrolledRef.current = true;
  }, [date, events]);

  /** The calendar date an offset on this timeline stores against. */
  const dateAt = (offset: number) =>
    offsetIsAfterMidnight(offset) ? nextDate : date;

  const commitEvent = useMutation({
    mutationFn: ({
      event,
      sourceDate,
      range,
    }: {
      event: CalendarEvent;
      sourceDate: string;
      range: EventTimeRange;
    }) => {
      const time = minutesToTime(wallMinutesFromOffset(range.startMinutes));
      const endTime = minutesToTime(wallMinutesFromOffset(range.endMinutes));
      // Dragging past the midnight rule changes which calendar date the event
      // is stored on, not just its clock time — sent only when it really moved,
      // so an ordinary retime doesn't rewrite the date field.
      const target = dateAt(range.startMinutes);
      const moved = target !== sourceDate;
      // A recurring instance only reschedules that one occurrence; a one-off
      // event is edited directly — same scoping EventDetails already applies
      // to a manual edit.
      return event.isRecurring
        ? api.calendar.moveOccurrence(
            event.id,
            event.occurrenceDate ?? event.date,
            {
              ...(moved ? { newDate: target } : {}),
              newTime: time,
              newEndTime: endTime,
            }
          )
        : api.calendar.update(event.id, {
            ...(moved ? { date: target } : {}),
            time,
            endTime,
          });
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['calendar'] }),
  });

  const createEvent = useMutation({
    mutationFn: () => {
      const now = new Date();
      // Placed at the current clock time wherever that falls on the timeline,
      // which past midnight means the *next* calendar date. Snapping in offset
      // minutes is the same as snapping the wall clock, since the 4am start is
      // itself a multiple of the half-hour step.
      const startOffset = Math.min(
        MINUTES_PER_DAY - DEFAULT_EVENT_DURATION_MINUTES,
        Math.round(
          offsetFromWallMinutes(now.getHours() * 60 + now.getMinutes()) / 30
        ) * 30
      );
      return api.calendar.create({
        title: 'New event',
        date: dateAt(startOffset),
        time: minutesToTime(wallMinutesFromOffset(startOffset)),
        endTime: minutesToTime(
          wallMinutesFromOffset(startOffset + DEFAULT_EVENT_DURATION_MINUTES)
        ),
        allDay: false,
      });
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['calendar'] }),
  });

  const handleCommit = (eventId: string, range: EventTimeRange) => {
    if (range.endMinutes - range.startMinutes < MIN_DURATION_MINUTES) return;
    const laid = laidOut.find(l => l.event.id === eventId);
    if (laid)
      commitEvent.mutate({
        event: laid.event,
        sourceDate: laid.sourceDate,
        range,
      });
  };

  return (
    <div className="flex-1 min-h-0 relative flex flex-col">
      {allDay.length > 0 && (
        <div className="shrink-0 flex flex-wrap gap-1 mb-2">
          {allDay.map(e => (
            <button
              key={e.id}
              onClick={() => onOpenEvent(e)}
              className="text-xs px-2 py-1 rounded bg-[var(--color-accent)]/10 text-[var(--color-accent)]"
            >
              {e.title}
            </button>
          ))}
        </div>
      )}

      <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto relative">
        <div
          className="relative"
          style={{ height: MINUTES_PER_DAY * PX_PER_MINUTE }}
        >
          {/* Drawn first so the hour grid and the events sit on top of them.
              A band is a surface, not a control: it takes a tap to open the
              editor, but the event layer above it is pointer-events-none
              except on the blocks themselves, so a line inside a shaded
              region still wins its own drags. */}
          {bands.map(band => (
            <button
              key={band.kind}
              type="button"
              data-testid={`sleep-band-${band.kind}`}
              onClick={onEditSleep}
              className="absolute left-0 right-0 bg-black/30 text-left"
              style={{
                top: band.startMinutes * PX_PER_MINUTE,
                height: (band.endMinutes - band.startMinutes) * PX_PER_MINUTE,
              }}
            >
              <span className="absolute left-12 top-1 text-[10px] text-[var(--color-text-muted)]">
                {band.label}
              </span>
            </button>
          ))}

          {DISPLAY_HOURS.map((h, i) => (
            <div
              key={h}
              // Transparent to pointers: an hour row is a rule, not a target,
              // and its label strip would otherwise punch a hole in the sleep
              // band behind it every 60px. Midnight gets a brighter rule: it is
              // where the calendar date under the timeline changes, and on a
              // 4am-to-4am grid it is no longer the obvious top edge.
              className={`absolute left-0 right-0 border-t pointer-events-none ${
                h === 0 ? 'border-white/20' : 'border-white/5'
              }`}
              style={{ top: i * 60 * PX_PER_MINUTE }}
            >
              <div className="w-12 shrink-0 -mt-2 text-[10px] text-[var(--color-text-muted)] text-right pr-1">
                {/* The topmost rule sits on the scroll edge, where a label
                    would be clipped in half. */}
                {i === 0
                  ? ''
                  : h === 0
                    ? '12am'
                    : h < 12
                      ? `${h}am`
                      : h === 12
                        ? '12pm'
                        : `${h - 12}pm`}
              </div>
            </div>
          ))}

          {/* pointer-events-none so the sleep bands underneath stay tappable
              across the whole row; each EventBlock turns them back on for
              itself. */}
          <div className="absolute left-12 right-2 top-0 bottom-0 pointer-events-none">
            <DayEventLayer
              events={laidOut}
              pxPerMinute={PX_PER_MINUTE}
              onOpenEvent={onOpenEvent}
              onCommit={handleCommit}
              onTranscribed={() =>
                queryClient.invalidateQueries({
                  queryKey: ['calendar', 'date', date],
                })
              }
            />
          </div>
        </div>
      </div>

      <button
        type="button"
        aria-label="New event"
        onClick={() => createEvent.mutate()}
        disabled={createEvent.isPending}
        className="absolute bottom-4 right-4 w-14 h-14 rounded-full bg-[var(--color-primary)] text-white text-2xl shadow-lg flex items-center justify-center disabled:opacity-50"
      >
        +
      </button>
    </div>
  );
}
