import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, type CalendarEvent } from '../hooks/api';
import { useIsMobile } from '@/hooks/useMediaQuery';
import {
  buildMonthGrid,
  eventTimeLabel,
  parseEventTags,
  repeatLabel,
  toLocalISO,
  WEEKDAY_INITIALS,
  WEEKDAY_LABELS,
  type RepeatFreq,
} from '@/lib/calendar';

type ViewMode = 'month' | 'week';

// A recurring series appears once per occurrence, all sharing the series id —
// so the React key has to include the date the instance landed on.
const instanceKey = (e: CalendarEvent) =>
  `${e.id}:${e.occurrenceDate ?? e.date}`;

function EventDetails({
  eventId,
  occurrenceDate,
  onClose,
}: {
  eventId: string;
  occurrenceDate?: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  // 'view' -> 'confirmDelete' asks which occurrences to remove; 'edit' ->
  // 'confirmScope' asks the same about a change. Both questions only exist for
  // a recurring event.
  const [mode, setMode] = useState<
    'view' | 'confirmDelete' | 'edit' | 'confirmScope'
  >('view');
  const [draft, setDraft] = useState({
    title: '',
    description: '',
    time: '',
    endTime: '',
    allDay: false,
  });

  const { data: event, isLoading } = useQuery({
    queryKey: ['calendar', 'event', eventId],
    queryFn: () => api.calendar.get(eventId),
  });

  const { data: relatedJournals } = useQuery({
    queryKey: ['calendar', 'related', occurrenceDate ?? event?.date],
    queryFn: () =>
      api.calendar.findRelatedJournals(occurrenceDate ?? event!.date),
    enabled: !!(occurrenceDate ?? event?.date),
  });

  const linkJournal = useMutation({
    mutationFn: ({
      calendarEventId,
      journalEntryId,
    }: {
      calendarEventId: string;
      journalEntryId: string;
    }) => api.calendar.linkJournal(calendarEventId, journalEntryId),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: ['calendar', 'event', eventId],
      }),
  });

  const unlinkJournal = useMutation({
    mutationFn: ({
      calendarEventId,
      journalEntryId,
    }: {
      calendarEventId: string;
      journalEntryId: string;
    }) => api.calendar.unlinkJournal(calendarEventId, journalEntryId),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: ['calendar', 'event', eventId],
      }),
  });

  const deleteEvent = useMutation({
    mutationFn: (id: string) => api.calendar.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['calendar'] });
      onClose();
    },
  });

  const skipOccurrence = useMutation({
    mutationFn: ({ id, date }: { id: string; date: string }) =>
      api.calendar.skipOccurrence(id, date),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['calendar'] });
      onClose();
    },
  });

  const endSeries = useMutation({
    mutationFn: ({ id, date }: { id: string; date: string }) =>
      api.calendar.endSeries(id, date),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['calendar'] });
      onClose();
    },
  });

  const saveEdit = useMutation({
    mutationFn: async ({
      id,
      date,
      scope,
    }: {
      id: string;
      date: string;
      scope: 'future' | 'all';
    }) => {
      const payload = {
        title: draft.title,
        description: draft.description || null,
        time: draft.allDay ? null : draft.time || null,
        endTime: draft.allDay ? null : draft.endTime || null,
        allDay: draft.allDay,
      };
      // The two endpoints return different shapes; the caller only cares that
      // the write landed.
      if (scope === 'all') {
        await api.calendar.update(id, payload);
      } else {
        await api.calendar.updateFrom(id, date, payload);
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['calendar'] });
      onClose();
    },
  });

  if (isLoading || !event) {
    return (
      <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
        <div className="bg-[var(--color-surface)] rounded-lg p-6 max-w-lg w-full mx-4">
          <div className="text-[var(--color-text-muted)]">Loading...</div>
        </div>
      </div>
    );
  }

  const linkedIds = new Set(event.linkedJournals?.map(j => j.id) || []);
  const unlinkedJournals =
    relatedJournals?.filter(j => !linkedIds.has(j.id)) || [];
  const shownDate = occurrenceDate ?? event.date;
  const repeat = repeatLabel(event);
  const busy =
    deleteEvent.isPending ||
    skipOccurrence.isPending ||
    endSeries.isPending ||
    saveEdit.isPending;

  const startEditing = () => {
    setDraft({
      title: event.title,
      description: event.description ?? '',
      time: event.time ?? '',
      endTime: event.endTime ?? '',
      allDay: !!event.allDay,
    });
    setMode('edit');
  };

  const commitEdit = (scope: 'future' | 'all') =>
    saveEdit.mutate({ id: event.id, date: shownDate, scope });

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50"
      onClick={onClose}
    >
      <div
        className="bg-[var(--color-surface)] rounded-lg p-6 max-w-lg w-full mx-4 max-h-[80vh] overflow-y-auto"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-start justify-between mb-4">
          <div>
            <h2 className="text-xl font-semibold text-[var(--color-text)]">
              {event.title}
            </h2>
            <div className="text-sm text-[var(--color-text-muted)] mt-1">
              {new Date(shownDate + 'T00:00:00').toLocaleDateString('en-US', {
                weekday: 'long',
                year: 'numeric',
                month: 'long',
                day: 'numeric',
              })}
              {eventTimeLabel(event) && (
                <span className="ml-2">{eventTimeLabel(event)}</span>
              )}
            </div>
            {repeat && (
              <div className="text-xs text-[var(--color-accent)] mt-1">
                ↻ {repeat}
                {event.repeatUntil && ` until ${event.repeatUntil}`}
              </div>
            )}
          </div>
          <button
            onClick={onClose}
            className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
          >
            ✕
          </button>
        </div>

        {mode === 'edit' || mode === 'confirmScope' ? (
          <div className="mb-4 space-y-2">
            <input
              type="text"
              aria-label="Title"
              value={draft.title}
              onChange={e => setDraft({ ...draft, title: e.target.value })}
              className="w-full bg-transparent text-[var(--color-text)] border-b border-white/10 pb-2 focus:outline-none"
            />
            <label className="flex items-center gap-2 text-sm text-[var(--color-text)]">
              <input
                type="checkbox"
                checked={draft.allDay}
                onChange={e => setDraft({ ...draft, allDay: e.target.checked })}
                className="w-4 h-4 accent-[var(--color-primary)]"
              />
              All day
            </label>
            {!draft.allDay && (
              <div className="flex gap-2">
                <input
                  type="time"
                  aria-label="Start time"
                  value={draft.time}
                  onChange={e => setDraft({ ...draft, time: e.target.value })}
                  className="flex-1 bg-transparent text-[var(--color-text)] border-b border-white/10 pb-2 focus:outline-none"
                />
                <input
                  type="time"
                  aria-label="End time"
                  value={draft.endTime}
                  onChange={e =>
                    setDraft({ ...draft, endTime: e.target.value })
                  }
                  className="flex-1 bg-transparent text-[var(--color-text)] border-b border-white/10 pb-2 focus:outline-none"
                />
              </div>
            )}
            <textarea
              aria-label="Description"
              value={draft.description}
              onChange={e =>
                setDraft({ ...draft, description: e.target.value })
              }
              placeholder="Description (optional)"
              rows={2}
              className="w-full bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] resize-none focus:outline-none"
            />
          </div>
        ) : (
          event.description && (
            <div className="mb-4 text-[var(--color-text)]">
              {event.description}
            </div>
          )
        )}

        {parseEventTags(event.tags).length > 0 && (
          <div className="flex gap-1 mb-4">
            {parseEventTags(event.tags).map(tag => (
              <span
                key={tag}
                className="px-2 py-0.5 text-xs bg-white/10 rounded text-[var(--color-text-muted)]"
              >
                {tag}
              </span>
            ))}
          </div>
        )}

        {event.linkedJournals && event.linkedJournals.length > 0 && (
          <div className="mb-4">
            <h3 className="text-sm font-medium text-[var(--color-text)] mb-2">
              Linked Journal Entries
            </h3>
            <div className="space-y-2">
              {event.linkedJournals.map(journal => (
                <div
                  key={journal.id}
                  className="p-3 bg-white/5 rounded-lg group"
                >
                  <div className="flex items-start justify-between">
                    <div className="flex-1">
                      <div className="font-medium text-[var(--color-text)]">
                        {journal.title || 'Untitled'}
                      </div>
                      <div className="text-sm text-[var(--color-text-muted)] line-clamp-2 mt-1">
                        {journal.content}
                      </div>
                    </div>
                    <button
                      onClick={() =>
                        unlinkJournal.mutate({
                          calendarEventId: event.id,
                          journalEntryId: journal.id,
                        })
                      }
                      className="text-red-400 hover:text-red-300 opacity-0 group-hover:opacity-100 ml-2"
                    >
                      Unlink
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {unlinkedJournals.length > 0 && (
          <div className="mb-4">
            <h3 className="text-sm font-medium text-[var(--color-text)] mb-2">
              Related Entries from This Day
            </h3>
            <div className="space-y-2">
              {unlinkedJournals.map(journal => (
                <div
                  key={journal.id}
                  className="p-3 bg-white/5 rounded-lg group"
                >
                  <div className="flex items-start justify-between">
                    <div className="flex-1">
                      <div className="font-medium text-[var(--color-text)]">
                        {journal.title || 'Untitled'}
                      </div>
                      <div className="text-sm text-[var(--color-text-muted)] line-clamp-2 mt-1">
                        {journal.content}
                      </div>
                    </div>
                    <button
                      onClick={() =>
                        linkJournal.mutate({
                          calendarEventId: event.id,
                          journalEntryId: journal.id,
                        })
                      }
                      className="text-[var(--color-primary)] hover:underline ml-2"
                    >
                      Link
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Anything touching a recurring event has to say *which* occurrences
            it means. Past days are a record of what actually happened, so
            "this and future" is the prominent choice and the retroactive
            "all events" is the deliberate one. */}
        {mode === 'confirmDelete' && (
          <p className="text-xs text-[var(--color-text-muted)] mb-2">
            Remove which occurrences?
          </p>
        )}
        {mode === 'confirmScope' && (
          <p className="text-xs text-[var(--color-text-muted)] mb-2">
            Apply the change to which occurrences?
          </p>
        )}

        <div className="flex flex-wrap justify-end gap-2 pt-4 border-t border-white/10">
          {mode === 'view' && (
            <>
              <button
                onClick={startEditing}
                className="px-3 py-1 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              >
                Edit
              </button>
              <button
                onClick={() =>
                  repeat
                    ? setMode('confirmDelete')
                    : deleteEvent.mutate(event.id)
                }
                disabled={busy}
                className="px-3 py-1 text-sm text-red-400 hover:text-red-300 disabled:opacity-50"
              >
                {repeat ? 'Delete' : 'Delete Event'}
              </button>
            </>
          )}

          {mode === 'confirmDelete' && (
            <>
              <button
                onClick={() =>
                  skipOccurrence.mutate({ id: event.id, date: shownDate })
                }
                disabled={busy}
                className="px-3 py-1 text-sm text-red-400 hover:text-red-300 disabled:opacity-50"
              >
                This occurrence
              </button>
              <button
                onClick={() =>
                  endSeries.mutate({ id: event.id, date: shownDate })
                }
                disabled={busy}
                className="px-3 py-1 text-sm text-red-400 hover:text-red-300 disabled:opacity-50"
              >
                This and future
              </button>
              <button
                onClick={() => deleteEvent.mutate(event.id)}
                disabled={busy}
                title="Erases the past occurrences too"
                className="px-3 py-1 text-sm text-red-400/70 hover:text-red-300 disabled:opacity-50"
              >
                Whole series
              </button>
            </>
          )}

          {mode === 'edit' && (
            <>
              <button
                onClick={() => setMode('view')}
                className="px-3 py-1 text-sm text-[var(--color-text-muted)]"
              >
                Cancel
              </button>
              <button
                onClick={() =>
                  repeat ? setMode('confirmScope') : commitEdit('all')
                }
                disabled={busy || !draft.title.trim()}
                className="px-3 py-1 text-sm bg-[var(--color-primary)] text-white rounded disabled:opacity-50"
              >
                Save
              </button>
            </>
          )}

          {mode === 'confirmScope' && (
            <>
              <button
                onClick={() => commitEdit('future')}
                disabled={busy}
                className="px-3 py-1 text-sm bg-[var(--color-primary)] text-white rounded disabled:opacity-50"
              >
                This and future
              </button>
              <button
                onClick={() => commitEdit('all')}
                disabled={busy}
                title="Rewrites the past occurrences too"
                className="px-3 py-1 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
              >
                All events
              </button>
            </>
          )}

          <button
            onClick={onClose}
            className="px-3 py-1 text-sm bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

const EMPTY_NEW_EVENT = {
  title: '',
  description: '',
  time: '',
  endTime: '',
  allDay: false,
  repeatFreq: '' as '' | RepeatFreq,
  repeatInterval: 1,
  repeatByweekday: [] as number[],
  repeatUntil: '',
};

export function Calendar() {
  const [currentDate, setCurrentDate] = useState(new Date());
  const [viewMode, setViewMode] = useState<ViewMode>('month');
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [selected, setSelected] = useState<{
    id: string;
    occurrenceDate: string;
  } | null>(null);
  const [newEvent, setNewEvent] = useState(EMPTY_NEW_EVENT);
  const [showNewEvent, setShowNewEvent] = useState(false);
  const isMobile = useIsMobile();
  const queryClient = useQueryClient();

  const year = currentDate.getFullYear();
  const month = currentDate.getMonth();
  const monthGrid = buildMonthGrid(year, month);
  // Fetch the whole 6-week grid, not just the month, so the leading/trailing
  // days from the neighbouring months show their events too.
  const gridStart = monthGrid[0].iso;
  const gridEnd = monthGrid[monthGrid.length - 1].iso;

  const { data: monthEvents } = useQuery({
    queryKey: ['calendar', 'range', gridStart, gridEnd],
    queryFn: () => api.calendar.listByRange(gridStart, gridEnd),
    enabled: viewMode === 'month',
  });

  const { data: weekEvents } = useQuery({
    queryKey: ['calendar', 'week', toLocalISO(currentDate)],
    queryFn: () => api.calendar.listByWeek(toLocalISO(currentDate)),
    enabled: viewMode === 'week',
  });

  const events = viewMode === 'month' ? monthEvents : weekEvents;

  const createEvent = useMutation({
    mutationFn: api.calendar.create,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['calendar'] });
      setNewEvent(EMPTY_NEW_EVENT);
      setShowNewEvent(false);
    },
  });

  const navigate = (direction: number) => {
    if (viewMode === 'month') {
      setCurrentDate(new Date(year, month + direction, 1));
    } else {
      const d = new Date(currentDate);
      d.setDate(currentDate.getDate() + direction * 7);
      setCurrentDate(d);
    }
  };

  const getEventsForDate = (date: string) =>
    events?.filter(e => e.date === date) || [];

  const monthName = currentDate.toLocaleString('default', {
    month: 'long',
    year: 'numeric',
  });

  const dayOfWeek = currentDate.getDay();
  const startOfWeek = new Date(currentDate);
  startOfWeek.setDate(currentDate.getDate() - dayOfWeek);
  const weekDays = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(startOfWeek);
    d.setDate(startOfWeek.getDate() + i);
    return d;
  });

  const todayISO = toLocalISO(new Date());
  const selectedEvents = selectedDate ? getEventsForDate(selectedDate) : [];

  const openEvent = (e: CalendarEvent) =>
    setSelected({ id: e.id, occurrenceDate: e.occurrenceDate ?? e.date });

  const submitNewEvent = () => {
    if (!selectedDate) return;
    createEvent.mutate({
      title: newEvent.title,
      date: selectedDate,
      description: newEvent.description || undefined,
      time: newEvent.allDay ? undefined : newEvent.time || undefined,
      endTime: newEvent.allDay ? undefined : newEvent.endTime || undefined,
      allDay: newEvent.allDay,
      repeatFreq: newEvent.repeatFreq || null,
      repeatInterval: newEvent.repeatFreq ? newEvent.repeatInterval : null,
      repeatByweekday:
        newEvent.repeatFreq === 'weekly' && newEvent.repeatByweekday.length
          ? newEvent.repeatByweekday
          : null,
      repeatUntil: newEvent.repeatUntil || null,
    });
  };

  const toggleWeekday = (day: number) =>
    setNewEvent(prev => ({
      ...prev,
      repeatByweekday: prev.repeatByweekday.includes(day)
        ? prev.repeatByweekday.filter(d => d !== day)
        : [...prev.repeatByweekday, day].sort((a, b) => a - b),
    }));

  return (
    <div className="flex-1 flex flex-col p-4 overflow-hidden">
      <div className="flex items-center justify-end mb-4">
        <div className="hidden md:flex gap-2">
          {(['month', 'week'] as ViewMode[]).map(v => (
            <button
              key={v}
              onClick={() => setViewMode(v)}
              className={`px-3 py-1 rounded text-sm ${viewMode === v ? 'bg-[var(--color-primary)] text-white' : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'}`}
            >
              {v.charAt(0).toUpperCase() + v.slice(1)}
            </button>
          ))}
        </div>
      </div>

      <div
        className={`flex-1 min-h-0 gap-4 ${isMobile ? 'flex flex-col overflow-y-auto' : 'flex overflow-hidden'}`}
      >
        <div
          className={`flex flex-col min-h-0 ${isMobile ? 'shrink-0' : 'flex-1'}`}
        >
          <div className="flex items-center justify-between mb-4 shrink-0">
            <button
              onClick={() => navigate(-1)}
              className="p-2 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            >
              ←
            </button>
            <h2 className="text-lg font-medium text-[var(--color-text)]">
              {viewMode === 'month'
                ? monthName
                : `Week of ${weekDays[0].toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`}
            </h2>
            <button
              onClick={() => navigate(1)}
              className="p-2 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            >
              →
            </button>
          </div>

          <div className="hidden md:grid grid-cols-7 gap-1 mb-2 shrink-0">
            {WEEKDAY_LABELS.map(day => (
              <div
                key={day}
                className="text-center text-sm text-[var(--color-text-muted)] py-1"
              >
                {day}
              </div>
            ))}
          </div>

          {/* Fixed 6 rows sharing whatever height is available. Cells must not
              be aspect-square: that sizes rows off the grid's *width*, which
              overflows the (clipped, non-scrolling) layout on a short window
              and silently hides the last week or two of the month. */}
          {!isMobile && viewMode === 'month' && (
            <div
              className="grid grid-cols-7 grid-rows-6 gap-1 flex-1 min-h-0"
              data-testid="month-grid"
            >
              {monthGrid.map(cell => {
                const dayEvents = getEventsForDate(cell.iso);
                const isSelected = selectedDate === cell.iso;
                const isToday = cell.iso === todayISO;
                return (
                  <div
                    key={cell.iso}
                    role="button"
                    tabIndex={0}
                    onClick={() => setSelectedDate(cell.iso)}
                    onKeyDown={e => {
                      if (e.key === 'Enter' || e.key === ' ')
                        setSelectedDate(cell.iso);
                    }}
                    data-testid="month-cell"
                    className={`h-full min-h-0 flex flex-col overflow-hidden p-1 rounded-lg border cursor-pointer transition-colors text-left ${isSelected ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/20' : 'border-transparent hover:border-white/20'} ${isToday ? 'bg-[var(--color-surface)]' : ''} ${cell.inMonth ? '' : 'opacity-40'}`}
                  >
                    <div
                      className={`text-sm shrink-0 ${isToday ? 'text-[var(--color-primary)] font-semibold' : 'text-[var(--color-text)]'}`}
                    >
                      {cell.day}
                    </div>
                    <div className="flex-1 min-h-0 overflow-y-auto mt-1 space-y-0.5">
                      {dayEvents.map(e => (
                        <button
                          key={instanceKey(e)}
                          onClick={ev => {
                            ev.stopPropagation();
                            openEvent(e);
                          }}
                          className="w-full text-xs truncate text-left text-[var(--color-accent)] bg-[var(--color-accent)]/10 rounded px-1 hover:bg-[var(--color-accent)]/20"
                        >
                          {e.isRecurring && (
                            <span className="opacity-70">↻ </span>
                          )}
                          {e.time && (
                            <span className="opacity-70">{e.time} </span>
                          )}
                          {e.title}
                        </button>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {!isMobile && viewMode === 'week' && (
            <div className="grid grid-cols-7 gap-2 flex-1 min-h-0">
              {weekDays.map(day => {
                const dateStr = toLocalISO(day);
                const dayEvents = getEventsForDate(dateStr);
                const isSelected = selectedDate === dateStr;
                const isToday = dateStr === todayISO;
                return (
                  <div
                    key={dateStr}
                    onClick={() => setSelectedDate(dateStr)}
                    className={`flex flex-col min-h-0 p-2 rounded-lg border cursor-pointer transition-colors ${isSelected ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/10' : 'border-white/10 hover:border-white/20'} ${isToday ? 'bg-[var(--color-surface)]' : ''}`}
                  >
                    <div
                      className={`text-center mb-2 shrink-0 ${isToday ? 'text-[var(--color-primary)]' : 'text-[var(--color-text)]'}`}
                    >
                      <div className="text-2xl font-semibold">
                        {day.getDate()}
                      </div>
                      <div className="text-xs text-[var(--color-text-muted)]">
                        {day.toLocaleDateString('en-US', { month: 'short' })}
                      </div>
                    </div>
                    <div className="flex-1 min-h-0 overflow-y-auto space-y-1">
                      {dayEvents.map(e => (
                        <button
                          key={instanceKey(e)}
                          onClick={ev => {
                            ev.stopPropagation();
                            openEvent(e);
                          }}
                          className="w-full text-left p-2 text-xs bg-[var(--color-accent)]/10 rounded hover:bg-[var(--color-accent)]/20 transition-colors"
                        >
                          <div className="font-medium text-[var(--color-text)] truncate">
                            {e.isRecurring && (
                              <span className="opacity-70">↻ </span>
                            )}
                            {e.title}
                          </div>
                          {eventTimeLabel(e) && (
                            <div className="text-[var(--color-text-muted)]">
                              {eventTimeLabel(e)}
                            </div>
                          )}
                        </button>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {isMobile && (
            <div className="space-y-1">
              {monthGrid
                .filter(cell => cell.inMonth)
                .map(cell => {
                  const dayEvents = getEventsForDate(cell.iso);
                  const isSelected = selectedDate === cell.iso;
                  const isToday = cell.iso === todayISO;
                  return (
                    <button
                      key={cell.iso}
                      onClick={() => setSelectedDate(cell.iso)}
                      className={`w-full flex items-start gap-3 p-2 rounded-lg border text-left min-h-[44px] transition-colors ${isSelected ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/10' : 'border-white/10 hover:border-white/20'} ${isToday ? 'bg-[var(--color-surface)]' : ''}`}
                    >
                      <div className="w-10 shrink-0 text-center">
                        <div
                          className={`text-lg font-semibold ${isToday ? 'text-[var(--color-primary)]' : 'text-[var(--color-text)]'}`}
                        >
                          {cell.day}
                        </div>
                        <div className="text-xs text-[var(--color-text-muted)]">
                          {cell.date.toLocaleDateString('en-US', {
                            weekday: 'short',
                          })}
                        </div>
                      </div>
                      <div className="flex-1 min-w-0 space-y-0.5 py-0.5">
                        {dayEvents.length === 0 ? (
                          <div className="text-xs text-[var(--color-text-muted)]">
                            No events
                          </div>
                        ) : (
                          dayEvents.map(e => (
                            <div
                              key={instanceKey(e)}
                              className="text-xs truncate text-[var(--color-accent)] bg-[var(--color-accent)]/10 rounded px-1 py-0.5"
                            >
                              {e.isRecurring && (
                                <span className="opacity-70">↻ </span>
                              )}
                              {e.time && (
                                <span className="opacity-70">{e.time} </span>
                              )}
                              {e.title}
                            </div>
                          ))
                        )}
                      </div>
                    </button>
                  );
                })}
            </div>
          )}
        </div>

        <div
          className={`${isMobile ? 'w-full shrink-0' : 'w-80 shrink-0'} bg-[var(--color-surface)] rounded-lg border border-white/10 p-4 overflow-y-auto`}
        >
          {selectedDate ? (
            <>
              <div className="flex items-center justify-between mb-4">
                <h3 className="font-medium text-[var(--color-text)]">
                  {new Date(selectedDate + 'T00:00:00').toLocaleDateString(
                    'en-US',
                    { weekday: 'long', month: 'long', day: 'numeric' }
                  )}
                </h3>
                <button
                  onClick={() => setShowNewEvent(!showNewEvent)}
                  className="text-sm text-[var(--color-primary)] hover:underline"
                >
                  + Add
                </button>
              </div>

              {showNewEvent && (
                <div className="mb-4 p-3 bg-white/5 rounded-lg">
                  <input
                    type="text"
                    value={newEvent.title}
                    onChange={e =>
                      setNewEvent({ ...newEvent, title: e.target.value })
                    }
                    placeholder="Event title"
                    className="w-full bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border-b border-white/10 pb-2 mb-2 focus:outline-none"
                  />
                  <label className="flex items-center gap-2 mb-2 text-xs text-[var(--color-text-muted)]">
                    <input
                      type="checkbox"
                      checked={newEvent.allDay}
                      onChange={e =>
                        setNewEvent({ ...newEvent, allDay: e.target.checked })
                      }
                      className="w-4 h-4 accent-[var(--color-primary)]"
                    />
                    All day
                  </label>
                  {!newEvent.allDay && (
                    <div className="flex gap-2 mb-2">
                      <input
                        type="time"
                        aria-label="Start time"
                        value={newEvent.time}
                        onChange={e =>
                          setNewEvent({ ...newEvent, time: e.target.value })
                        }
                        className="flex-1 bg-transparent text-[var(--color-text)] border-b border-white/10 pb-2 focus:outline-none"
                      />
                      <input
                        type="time"
                        aria-label="End time"
                        value={newEvent.endTime}
                        onChange={e =>
                          setNewEvent({ ...newEvent, endTime: e.target.value })
                        }
                        className="flex-1 bg-transparent text-[var(--color-text)] border-b border-white/10 pb-2 focus:outline-none"
                      />
                    </div>
                  )}

                  <div className="mb-2">
                    <label className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
                      Repeats
                      <select
                        value={newEvent.repeatFreq}
                        onChange={e =>
                          setNewEvent({
                            ...newEvent,
                            repeatFreq: e.target.value as '' | RepeatFreq,
                            // Seed weekly repeats with the selected day so a
                            // bare "weekly" still means something.
                            repeatByweekday:
                              e.target.value === 'weekly' &&
                              newEvent.repeatByweekday.length === 0
                                ? [
                                    new Date(
                                      selectedDate + 'T00:00:00'
                                    ).getDay(),
                                  ]
                                : newEvent.repeatByweekday,
                          })
                        }
                        className="flex-1 bg-transparent text-[var(--color-text)] border-b border-white/10 pb-1 focus:outline-none"
                      >
                        <option value="">Never</option>
                        <option value="daily">Daily</option>
                        <option value="weekly">Weekly</option>
                        <option value="monthly">Monthly</option>
                        <option value="yearly">Yearly</option>
                      </select>
                    </label>

                    {newEvent.repeatFreq && (
                      <div className="mt-2 space-y-2">
                        <label className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
                          Every
                          <input
                            type="number"
                            min={1}
                            value={newEvent.repeatInterval}
                            onChange={e =>
                              setNewEvent({
                                ...newEvent,
                                repeatInterval: Math.max(
                                  1,
                                  Number(e.target.value) || 1
                                ),
                              })
                            }
                            className="w-14 bg-transparent text-[var(--color-text)] border-b border-white/10 focus:outline-none"
                          />
                          {newEvent.repeatFreq === 'daily'
                            ? 'day(s)'
                            : newEvent.repeatFreq === 'weekly'
                              ? 'week(s)'
                              : 'month(s)'}
                        </label>

                        {newEvent.repeatFreq === 'weekly' && (
                          <div className="flex gap-1">
                            {WEEKDAY_INITIALS.map((initial, day) => (
                              <button
                                key={day}
                                type="button"
                                aria-label={WEEKDAY_LABELS[day]}
                                aria-pressed={newEvent.repeatByweekday.includes(
                                  day
                                )}
                                onClick={() => toggleWeekday(day)}
                                className={`w-7 h-7 rounded text-xs ${
                                  newEvent.repeatByweekday.includes(day)
                                    ? 'bg-[var(--color-primary)] text-white'
                                    : 'bg-white/5 text-[var(--color-text-muted)] hover:bg-white/10'
                                }`}
                              >
                                {initial}
                              </button>
                            ))}
                          </div>
                        )}

                        <label className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
                          Until
                          <input
                            type="date"
                            value={newEvent.repeatUntil}
                            min={selectedDate}
                            onChange={e =>
                              setNewEvent({
                                ...newEvent,
                                repeatUntil: e.target.value,
                              })
                            }
                            className="flex-1 bg-transparent text-[var(--color-text)] border-b border-white/10 focus:outline-none"
                          />
                        </label>
                      </div>
                    )}
                  </div>

                  <textarea
                    value={newEvent.description}
                    onChange={e =>
                      setNewEvent({ ...newEvent, description: e.target.value })
                    }
                    placeholder="Description (optional)"
                    rows={2}
                    className="w-full bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] resize-none focus:outline-none"
                  />
                  <div className="flex justify-end gap-2 mt-2">
                    <button
                      onClick={() => setShowNewEvent(false)}
                      className="px-2 py-1 text-sm text-[var(--color-text-muted)]"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={submitNewEvent}
                      disabled={!newEvent.title.trim() || createEvent.isPending}
                      className="px-2 py-1 text-sm bg-[var(--color-primary)] text-white rounded disabled:opacity-50"
                    >
                      Save
                    </button>
                  </div>
                </div>
              )}

              <div className="space-y-2">
                {selectedEvents.map(event => (
                  <button
                    key={instanceKey(event)}
                    onClick={() => openEvent(event)}
                    className="w-full text-left p-3 bg-white/5 rounded-lg hover:bg-white/10 transition-colors"
                  >
                    <div className="font-medium text-[var(--color-text)]">
                      {event.title}
                    </div>
                    {eventTimeLabel(event) && (
                      <div className="text-sm text-[var(--color-text-muted)]">
                        {eventTimeLabel(event)}
                      </div>
                    )}
                    {event.isRecurring && (
                      <div className="text-xs text-[var(--color-accent)] mt-1">
                        ↻ {repeatLabel(event)}
                      </div>
                    )}
                    {event.description && (
                      <div className="text-sm text-[var(--color-text-muted)] mt-1 line-clamp-2">
                        {event.description}
                      </div>
                    )}
                    {event.journalId && (
                      <div className="text-xs text-[var(--color-accent)] mt-1">
                        Has linked journal
                      </div>
                    )}
                  </button>
                ))}
                {selectedEvents.length === 0 && !showNewEvent && (
                  <div className="text-sm text-[var(--color-text-muted)] text-center py-4">
                    No events for this day
                  </div>
                )}
              </div>
            </>
          ) : (
            <div className="text-[var(--color-text-muted)] text-center py-4">
              Select a date to view events
            </div>
          )}
        </div>
      </div>

      {selected && (
        <EventDetails
          eventId={selected.id}
          occurrenceDate={selected.occurrenceDate}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}
