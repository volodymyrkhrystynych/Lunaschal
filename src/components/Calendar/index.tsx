import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, type CalendarEvent } from '../../hooks/api';
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
// The shared splitter — Food's two views each grew a private copy of this and
// a third would make the rule genuinely untraceable.
import { parseTagsInput } from '@/lib/tags';
import { EventDetails } from './EventDetails';
import { DayView } from './DayView';
import { SleepEditor } from './SleepEditor';

type ViewMode = 'month' | 'week';
// Which screen the mobile layout shows: 'day' is the default hour-grid
// timeline; 'month' is a date picker (the pre-existing agenda list) whose
// only job now is choosing a day to jump into.
type MobileView = 'day' | 'month';

// A recurring series appears once per occurrence, all sharing the series id —
// so the React key has to include the date the instance landed on.
const instanceKey = (e: CalendarEvent) =>
  `${e.id}:${e.occurrenceDate ?? e.date}`;

const EMPTY_NEW_EVENT = {
  title: '',
  description: '',
  time: '',
  endTime: '',
  allDay: false,
  // Comma-separated while it is being typed; `parseTagsInput` splits it on
  // submit and the backend owns normalization from there.
  tags: '',
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
  const [tagFilter, setTagFilter] = useState<string | null>(null);
  const [showNewEvent, setShowNewEvent] = useState(false);
  const isMobile = useIsMobile();
  const queryClient = useQueryClient();

  const todayISO = toLocalISO(new Date());
  const [mobileView, setMobileView] = useState<MobileView>('day');
  const [dayDate, setDayDate] = useState(todayISO);
  const [editingSleep, setEditingSleep] = useState(false);

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

  const { data: tagCounts } = useQuery({
    queryKey: ['calendar', 'tags'],
    queryFn: api.calendar.tags,
  });

  const fetched = viewMode === 'month' ? monthEvents : weekEvents;
  // Filtering here rather than in the query: the month and week grids both
  // read off `events`, so one filter covers every place an event is drawn.
  const events = tagFilter
    ? fetched?.filter(e => parseEventTags(e.tags).includes(tagFilter))
    : fetched;

  const createEvent = useMutation({
    mutationFn: api.calendar.create,
    onSuccess: () => {
      // The 'calendar' prefix covers the tags query too, so a tag typed on a
      // brand-new event shows up in the pill row without a reload.
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

  const navigateDay = (direction: number) => {
    const d = new Date(dayDate + 'T00:00:00');
    d.setDate(d.getDate() + direction);
    setDayDate(toLocalISO(d));
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

  const selectedEvents = selectedDate ? getEventsForDate(selectedDate) : [];

  const dayLabel =
    dayDate === todayISO
      ? 'Today'
      : new Date(dayDate + 'T00:00:00').toLocaleDateString('en-US', {
          weekday: 'short',
          month: 'short',
          day: 'numeric',
        });

  const openEvent = (e: CalendarEvent) =>
    setSelected({ id: e.id, occurrenceDate: e.occurrenceDate ?? e.date });

  const jumpToDay = (iso: string) => {
    setDayDate(iso);
    setMobileView('day');
  };

  const submitNewEvent = () => {
    if (!selectedDate) return;
    createEvent.mutate({
      title: newEvent.title,
      date: selectedDate,
      description: newEvent.description || undefined,
      time: newEvent.allDay ? undefined : newEvent.time || undefined,
      endTime: newEvent.allDay ? undefined : newEvent.endTime || undefined,
      allDay: newEvent.allDay,
      tags: parseTagsInput(newEvent.tags),
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

  const showingDayView = isMobile && mobileView === 'day';

  return (
    <div className="flex-1 flex flex-col p-4 overflow-hidden">
      {showingDayView ? (
        <div className="flex items-center justify-between mb-4 shrink-0">
          <button
            onClick={() => setMobileView('month')}
            className="px-3 py-2 min-h-[44px] rounded text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-white/5"
          >
            ☰ Month
          </button>
          <div className="flex items-center gap-1">
            <button
              onClick={() => navigateDay(-1)}
              className="p-2 min-h-[44px] min-w-[44px] text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              aria-label="Previous day"
            >
              ←
            </button>
            <button
              onClick={() => setDayDate(todayISO)}
              className="px-2 py-2 min-h-[44px] text-sm font-medium text-[var(--color-text)]"
            >
              {dayLabel}
            </button>
            <button
              onClick={() => navigateDay(1)}
              className="p-2 min-h-[44px] min-w-[44px] text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              aria-label="Next day"
            >
              →
            </button>
          </div>
          {/* The way in on a day with no bands drawn — no activity recorded and
              nothing set by hand — where there would otherwise be nothing to
              tap. */}
          <button
            onClick={() => setEditingSleep(true)}
            aria-label="Edit sleep times"
            className="p-2 min-h-[44px] min-w-[44px] text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
          >
            ☾
          </button>
        </div>
      ) : (
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
      )}

      {showingDayView ? (
        <DayView
          date={dayDate}
          onOpenEvent={openEvent}
          onEditSleep={() => setEditingSleep(true)}
        />
      ) : (
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

            {/* Fed by the whole table, not the month on screen — a filter
                offering only the tags of what you are already looking at
                cannot be used to go and find the rest. */}
            {!!tagCounts?.length && (
              <div className="tag-row flex gap-1 mb-3 shrink-0">
                {tagFilter && (
                  <button
                    onClick={() => setTagFilter(null)}
                    className="px-2 py-0.5 text-xs rounded bg-white/10 text-[var(--color-text-muted)] hover:bg-white/15"
                  >
                    Clear
                  </button>
                )}
                {tagCounts.map(tag => (
                  <button
                    key={tag.name}
                    onClick={() =>
                      setTagFilter(tagFilter === tag.name ? null : tag.name)
                    }
                    className={`px-2 py-0.5 text-xs rounded ${
                      tagFilter === tag.name
                        ? 'bg-[var(--color-primary)] text-white'
                        : 'bg-white/10 text-[var(--color-text-muted)] hover:bg-white/15'
                    }`}
                  >
                    {tag.name} {tag.count}
                  </button>
                ))}
              </div>
            )}

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
                    const isToday = cell.iso === todayISO;
                    return (
                      <button
                        key={cell.iso}
                        onClick={() => jumpToDay(cell.iso)}
                        className={`w-full flex items-start gap-3 p-2 rounded-lg border text-left min-h-[44px] transition-colors border-white/10 hover:border-white/20 ${isToday ? 'bg-[var(--color-surface)]' : ''}`}
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

          {!isMobile && (
            <div className="w-80 shrink-0 bg-[var(--color-surface)] rounded-lg border border-white/10 p-4 overflow-y-auto">
              {selectedDate ? (
                <>
                  <div className="flex items-center justify-between mb-4">
                    <h3 className="font-medium text-[var(--color-text)]">
                      {new Date(selectedDate + 'T00:00:00').toLocaleDateString(
                        'en-US',
                        {
                          weekday: 'long',
                          month: 'long',
                          day: 'numeric',
                        }
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
                            setNewEvent({
                              ...newEvent,
                              allDay: e.target.checked,
                            })
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
                              setNewEvent({
                                ...newEvent,
                                time: e.target.value,
                              })
                            }
                            className="flex-1 bg-transparent text-[var(--color-text)] border-b border-white/10 pb-2 focus:outline-none"
                          />
                          <input
                            type="time"
                            aria-label="End time"
                            value={newEvent.endTime}
                            onChange={e =>
                              setNewEvent({
                                ...newEvent,
                                endTime: e.target.value,
                              })
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
                          setNewEvent({
                            ...newEvent,
                            description: e.target.value,
                          })
                        }
                        placeholder="Description (optional)"
                        rows={2}
                        className="w-full bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] resize-none focus:outline-none"
                      />
                      <input
                        value={newEvent.tags}
                        onChange={e =>
                          setNewEvent({ ...newEvent, tags: e.target.value })
                        }
                        placeholder="Tags (comma-separated)"
                        className="w-full bg-transparent text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none"
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
                          disabled={
                            !newEvent.title.trim() || createEvent.isPending
                          }
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
          )}
        </div>
      )}

      {selected && (
        <EventDetails
          eventId={selected.id}
          occurrenceDate={selected.occurrenceDate}
          onClose={() => setSelected(null)}
        />
      )}

      {editingSleep && (
        <SleepEditor date={dayDate} onClose={() => setEditingSleep(false)} />
      )}
    </div>
  );
}
