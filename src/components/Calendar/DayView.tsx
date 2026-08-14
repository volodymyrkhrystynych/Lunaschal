import { useEffect, useMemo, useRef } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type CalendarEvent } from '../../hooks/api';
import {
  DEFAULT_EVENT_DURATION_MINUTES,
  MIN_DURATION_MINUTES,
  MINUTES_PER_DAY,
  computeOverlapDepth,
  minutesToTime,
  timeToMinutes,
  type EventTimeRange,
} from '@/lib/calendarDayLayout';
import { localDayKey } from '@/lib/dates';
import { sleepBands } from '@/lib/sleep';
import { DayEventLayer, type LaidOutEvent } from './DayEventLayer';

/** 60px-tall hour rows. */
const PX_PER_MINUTE = 1;
const HOURS = Array.from({ length: 24 }, (_, h) => h);

interface DayViewProps {
  date: string;
  onOpenEvent: (event: CalendarEvent) => void;
  onEditSleep: () => void;
}

export function DayView({ date, onOpenEvent, onEditSleep }: DayViewProps) {
  const queryClient = useQueryClient();
  const scrollRef = useRef<HTMLDivElement>(null);
  const scrolledRef = useRef(false);

  // Polls while something is transcribed but not yet classified, so the
  // category border appears as soon as the background job finishes without
  // needing a dedicated SSE stream for one small field.
  const { data: events } = useQuery({
    queryKey: ['calendar', 'date', date],
    queryFn: () => api.calendar.listByDate(date),
    refetchInterval: query => {
      const list = query.state.data as CalendarEvent[] | undefined;
      return list?.some(e => e.description && !e.classifiedAt) ? 2000 : false;
    },
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
    () => (events || []).filter(e => !e.allDay && e.time),
    [events]
  );
  const allDay = useMemo(() => (events || []).filter(e => e.allDay), [events]);

  const laidOut: LaidOutEvent[] = useMemo(() => {
    const withRanges = timed.map(event => {
      const startMinutes = timeToMinutes(event.time!);
      const endMinutes = event.endTime
        ? timeToMinutes(event.endTime)
        : startMinutes + DEFAULT_EVENT_DURATION_MINUTES;
      return { event, range: { startMinutes, endMinutes } };
    });
    const depths = computeOverlapDepth(
      withRanges.map(w => ({ id: w.event.id, ...w.range }))
    );
    return withRanges.map(w => ({
      event: w.event,
      range: w.range,
      depth: depths.get(w.event.id) ?? 0,
    }));
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
    const targetMinutes = isToday ? now.getHours() * 60 : 8 * 60;
    scrollRef.current.scrollTop = Math.max(
      0,
      targetMinutes * PX_PER_MINUTE - 120
    );
    scrolledRef.current = true;
  }, [date, events]);

  const commitEvent = useMutation({
    mutationFn: ({
      event,
      range,
    }: {
      event: CalendarEvent;
      range: EventTimeRange;
    }) => {
      const time = minutesToTime(range.startMinutes);
      const endTime = minutesToTime(range.endMinutes);
      // A recurring instance only reschedules that one occurrence; a one-off
      // event is edited directly — same scoping EventDetails already applies
      // to a manual edit.
      return event.isRecurring
        ? api.calendar.moveOccurrence(
            event.id,
            event.occurrenceDate ?? event.date,
            { newTime: time, newEndTime: endTime }
          )
        : api.calendar.update(event.id, { time, endTime });
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['calendar'] }),
  });

  const createEvent = useMutation({
    mutationFn: () => {
      const now = new Date();
      const nowMinutes = now.getHours() * 60 + now.getMinutes();
      const startMinutes = Math.min(
        MINUTES_PER_DAY - DEFAULT_EVENT_DURATION_MINUTES,
        Math.round(nowMinutes / 30) * 30
      );
      return api.calendar.create({
        title: 'New event',
        date,
        time: minutesToTime(startMinutes),
        endTime: minutesToTime(startMinutes + DEFAULT_EVENT_DURATION_MINUTES),
        allDay: false,
      });
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['calendar'] }),
  });

  const handleCommit = (eventId: string, range: EventTimeRange) => {
    if (range.endMinutes - range.startMinutes < MIN_DURATION_MINUTES) return;
    const laid = laidOut.find(l => l.event.id === eventId);
    if (laid) commitEvent.mutate({ event: laid.event, range });
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

          {HOURS.map(h => (
            <div
              key={h}
              // Transparent to pointers: an hour row is a rule, not a target,
              // and its label strip would otherwise punch a hole in the sleep
              // band behind it every 60px.
              className="absolute left-0 right-0 border-t border-white/5 pointer-events-none"
              style={{ top: h * 60 * PX_PER_MINUTE }}
            >
              <div className="w-12 shrink-0 -mt-2 text-[10px] text-[var(--color-text-muted)] text-right pr-1">
                {h === 0
                  ? ''
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
