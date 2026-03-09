import { useState } from 'react';
import { trpc } from '../hooks/trpc';

type ViewMode = 'month' | 'week';

interface EventDetailsProps {
  eventId: string;
  onClose: () => void;
}

function EventDetails({ eventId, onClose }: EventDetailsProps) {
  const utils = trpc.useUtils();
  const { data: event, isLoading } = trpc.calendar.get.useQuery({ id: eventId });
  const { data: relatedJournals } = trpc.calendar.findRelatedJournals.useQuery(
    { date: event?.date || '' },
    { enabled: !!event?.date }
  );

  const linkJournal = trpc.calendar.linkJournal.useMutation({
    onSuccess: () => utils.calendar.get.invalidate({ id: eventId }),
  });

  const unlinkJournal = trpc.calendar.unlinkJournal.useMutation({
    onSuccess: () => utils.calendar.get.invalidate({ id: eventId }),
  });

  const deleteEvent = trpc.calendar.delete.useMutation({
    onSuccess: () => {
      utils.calendar.listByRange.invalidate();
      utils.calendar.listByWeek.invalidate();
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

  const linkedIds = new Set(event.linkedJournals?.map((j) => j.id) || []);
  const unlinkedJournals = relatedJournals?.filter((j) => !linkedIds.has(j.id)) || [];

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-[var(--color-surface)] rounded-lg p-6 max-w-lg w-full mx-4 max-h-[80vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between mb-4">
          <div>
            <h2 className="text-xl font-semibold text-[var(--color-text)]">{event.title}</h2>
            <div className="text-sm text-[var(--color-text-muted)] mt-1">
              {new Date(event.date + 'T00:00:00').toLocaleDateString('en-US', {
                weekday: 'long',
                year: 'numeric',
                month: 'long',
                day: 'numeric',
              })}
              {event.time && <span className="ml-2">{event.time}</span>}
              {event.endTime && <span> - {event.endTime}</span>}
            </div>
          </div>
          <button onClick={onClose} className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
            ✕
          </button>
        </div>

        {event.description && (
          <div className="mb-4 text-[var(--color-text)]">{event.description}</div>
        )}

        {event.tags && (
          <div className="flex gap-1 mb-4">
            {JSON.parse(event.tags).map((tag: string) => (
              <span key={tag} className="px-2 py-0.5 text-xs bg-white/10 rounded text-[var(--color-text-muted)]">
                {tag}
              </span>
            ))}
          </div>
        )}

        {/* Linked Journal Entries */}
        {event.linkedJournals && event.linkedJournals.length > 0 && (
          <div className="mb-4">
            <h3 className="text-sm font-medium text-[var(--color-text)] mb-2">Linked Journal Entries</h3>
            <div className="space-y-2">
              {event.linkedJournals.map((journal) => (
                <div key={journal.id} className="p-3 bg-white/5 rounded-lg group">
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

        {/* Related Journal Entries (not yet linked) */}
        {unlinkedJournals.length > 0 && (
          <div className="mb-4">
            <h3 className="text-sm font-medium text-[var(--color-text)] mb-2">
              Related Entries from This Day
            </h3>
            <div className="space-y-2">
              {unlinkedJournals.map((journal) => (
                <div key={journal.id} className="p-3 bg-white/5 rounded-lg group">
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

        <div className="flex justify-end gap-2 pt-4 border-t border-white/10">
          <button
            onClick={() => deleteEvent.mutate({ id: event.id })}
            disabled={deleteEvent.isPending}
            className="px-3 py-1 text-sm text-red-400 hover:text-red-300 disabled:opacity-50"
          >
            Delete Event
          </button>
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

export function Calendar() {
  const [currentDate, setCurrentDate] = useState(new Date());
  const [viewMode, setViewMode] = useState<ViewMode>('month');
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [newEvent, setNewEvent] = useState({ title: '', description: '', time: '', endTime: '' });
  const [showNewEvent, setShowNewEvent] = useState(false);

  const utils = trpc.useUtils();

  // Month view data
  const startOfMonth = new Date(currentDate.getFullYear(), currentDate.getMonth(), 1);
  const endOfMonth = new Date(currentDate.getFullYear(), currentDate.getMonth() + 1, 0);
  const formatDateISO = (date: Date) => date.toISOString().split('T')[0];

  const { data: monthEvents } = trpc.calendar.listByRange.useQuery(
    { startDate: formatDateISO(startOfMonth), endDate: formatDateISO(endOfMonth) },
    { enabled: viewMode === 'month' }
  );

  // Week view data
  const { data: weekEvents } = trpc.calendar.listByWeek.useQuery(
    { date: formatDateISO(currentDate) },
    { enabled: viewMode === 'week' }
  );

  const events = viewMode === 'month' ? monthEvents : weekEvents;

  const createEvent = trpc.calendar.create.useMutation({
    onSuccess: () => {
      utils.calendar.listByRange.invalidate();
      utils.calendar.listByWeek.invalidate();
      setNewEvent({ title: '', description: '', time: '', endTime: '' });
      setShowNewEvent(false);
    },
  });

  const handleCreate = () => {
    if (!newEvent.title.trim() || !selectedDate) return;
    createEvent.mutate({
      title: newEvent.title,
      description: newEvent.description || undefined,
      date: selectedDate,
      time: newEvent.time || undefined,
      endTime: newEvent.endTime || undefined,
    });
  };

  // Calendar calculations
  const daysInMonth = endOfMonth.getDate();
  const firstDayOfMonth = startOfMonth.getDay();
  const monthName = currentDate.toLocaleString('default', { month: 'long', year: 'numeric' });

  // Week view calculations
  const dayOfWeek = currentDate.getDay();
  const startOfWeek = new Date(currentDate);
  startOfWeek.setDate(currentDate.getDate() - dayOfWeek);
  const weekDays = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(startOfWeek);
    d.setDate(startOfWeek.getDate() + i);
    return d;
  });

  const navigate = (direction: number) => {
    if (viewMode === 'month') {
      setCurrentDate(new Date(currentDate.getFullYear(), currentDate.getMonth() + direction, 1));
    } else {
      const newDate = new Date(currentDate);
      newDate.setDate(currentDate.getDate() + direction * 7);
      setCurrentDate(newDate);
    }
  };

  const getEventsForDate = (date: string) => events?.filter((e) => e.date === date) || [];

  const days = [];
  for (let i = 0; i < firstDayOfMonth; i++) days.push(null);
  for (let i = 1; i <= daysInMonth; i++) days.push(i);

  const selectedEvents = selectedDate ? getEventsForDate(selectedDate) : [];

  return (
    <div className="flex-1 flex flex-col p-4 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-semibold text-[var(--color-text)]">Calendar</h1>
        <div className="flex gap-2">
          <button
            onClick={() => setViewMode('month')}
            className={`px-3 py-1 rounded text-sm ${
              viewMode === 'month'
                ? 'bg-[var(--color-primary)] text-white'
                : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
            }`}
          >
            Month
          </button>
          <button
            onClick={() => setViewMode('week')}
            className={`px-3 py-1 rounded text-sm ${
              viewMode === 'week'
                ? 'bg-[var(--color-primary)] text-white'
                : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
            }`}
          >
            Week
          </button>
        </div>
      </div>

      <div className="flex-1 flex gap-4 overflow-hidden">
        {/* Calendar Grid */}
        <div className="flex-1 flex flex-col">
          {/* Navigation */}
          <div className="flex items-center justify-between mb-4">
            <button onClick={() => navigate(-1)} className="p-2 text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
              ←
            </button>
            <h2 className="text-lg font-medium text-[var(--color-text)]">
              {viewMode === 'month'
                ? monthName
                : `Week of ${weekDays[0].toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`}
            </h2>
            <button onClick={() => navigate(1)} className="p-2 text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
              →
            </button>
          </div>

          {/* Day Headers */}
          <div className="grid grid-cols-7 gap-1 mb-2">
            {['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map((day) => (
              <div key={day} className="text-center text-sm text-[var(--color-text-muted)] py-2">
                {day}
              </div>
            ))}
          </div>

          {/* Month View */}
          {viewMode === 'month' && (
            <div className="grid grid-cols-7 gap-1 flex-1">
              {days.map((day, index) => {
                if (day === null) return <div key={`empty-${index}`} className="aspect-square" />;

                const dateStr = formatDateISO(new Date(currentDate.getFullYear(), currentDate.getMonth(), day));
                const dayEvents = getEventsForDate(dateStr);
                const isSelected = selectedDate === dateStr;
                const isToday = dateStr === formatDateISO(new Date());

                return (
                  <button
                    key={day}
                    onClick={() => setSelectedDate(dateStr)}
                    className={`aspect-square p-1 rounded-lg border transition-colors text-left ${
                      isSelected
                        ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/20'
                        : 'border-transparent hover:border-white/20'
                    } ${isToday ? 'bg-[var(--color-surface)]' : ''}`}
                  >
                    <div className={`text-sm ${isToday ? 'text-[var(--color-primary)] font-semibold' : 'text-[var(--color-text)]'}`}>
                      {day}
                    </div>
                    {dayEvents.length > 0 && (
                      <div className="mt-1 space-y-0.5">
                        {dayEvents.slice(0, 2).map((e) => (
                          <div
                            key={e.id}
                            className="text-xs truncate text-[var(--color-accent)] bg-[var(--color-accent)]/10 rounded px-1"
                          >
                            {e.time && <span className="opacity-70">{e.time} </span>}
                            {e.title}
                          </div>
                        ))}
                        {dayEvents.length > 2 && (
                          <div className="text-xs text-[var(--color-text-muted)]">+{dayEvents.length - 2} more</div>
                        )}
                      </div>
                    )}
                  </button>
                );
              })}
            </div>
          )}

          {/* Week View */}
          {viewMode === 'week' && (
            <div className="grid grid-cols-7 gap-2 flex-1">
              {weekDays.map((day) => {
                const dateStr = formatDateISO(day);
                const dayEvents = getEventsForDate(dateStr);
                const isSelected = selectedDate === dateStr;
                const isToday = dateStr === formatDateISO(new Date());

                return (
                  <div
                    key={dateStr}
                    onClick={() => setSelectedDate(dateStr)}
                    className={`p-2 rounded-lg border cursor-pointer transition-colors ${
                      isSelected
                        ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/10'
                        : 'border-white/10 hover:border-white/20'
                    } ${isToday ? 'bg-[var(--color-surface)]' : ''}`}
                  >
                    <div className={`text-center mb-2 ${isToday ? 'text-[var(--color-primary)]' : 'text-[var(--color-text)]'}`}>
                      <div className="text-2xl font-semibold">{day.getDate()}</div>
                      <div className="text-xs text-[var(--color-text-muted)]">
                        {day.toLocaleDateString('en-US', { month: 'short' })}
                      </div>
                    </div>
                    <div className="space-y-1">
                      {dayEvents.map((e) => (
                        <button
                          key={e.id}
                          onClick={(ev) => {
                            ev.stopPropagation();
                            setSelectedEventId(e.id);
                          }}
                          className="w-full text-left p-2 text-xs bg-[var(--color-accent)]/10 rounded hover:bg-[var(--color-accent)]/20 transition-colors"
                        >
                          <div className="font-medium text-[var(--color-text)] truncate">{e.title}</div>
                          {e.time && <div className="text-[var(--color-text-muted)]">{e.time}</div>}
                        </button>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Selected Date Panel */}
        <div className="w-80 bg-[var(--color-surface)] rounded-lg border border-white/10 p-4 overflow-y-auto">
          {selectedDate ? (
            <>
              <div className="flex items-center justify-between mb-4">
                <h3 className="font-medium text-[var(--color-text)]">
                  {new Date(selectedDate + 'T00:00:00').toLocaleDateString('en-US', {
                    weekday: 'long',
                    month: 'long',
                    day: 'numeric',
                  })}
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
                    onChange={(e) => setNewEvent({ ...newEvent, title: e.target.value })}
                    placeholder="Event title"
                    className="w-full bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border-b border-white/10 pb-2 mb-2 focus:outline-none"
                  />
                  <div className="flex gap-2 mb-2">
                    <input
                      type="time"
                      value={newEvent.time}
                      onChange={(e) => setNewEvent({ ...newEvent, time: e.target.value })}
                      placeholder="Start"
                      className="flex-1 bg-transparent text-[var(--color-text)] border-b border-white/10 pb-2 focus:outline-none"
                    />
                    <input
                      type="time"
                      value={newEvent.endTime}
                      onChange={(e) => setNewEvent({ ...newEvent, endTime: e.target.value })}
                      placeholder="End"
                      className="flex-1 bg-transparent text-[var(--color-text)] border-b border-white/10 pb-2 focus:outline-none"
                    />
                  </div>
                  <textarea
                    value={newEvent.description}
                    onChange={(e) => setNewEvent({ ...newEvent, description: e.target.value })}
                    placeholder="Description (optional)"
                    rows={2}
                    className="w-full bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] resize-none focus:outline-none"
                  />
                  <div className="flex justify-end gap-2 mt-2">
                    <button onClick={() => setShowNewEvent(false)} className="px-2 py-1 text-sm text-[var(--color-text-muted)]">
                      Cancel
                    </button>
                    <button
                      onClick={handleCreate}
                      disabled={!newEvent.title.trim() || createEvent.isPending}
                      className="px-2 py-1 text-sm bg-[var(--color-primary)] text-white rounded disabled:opacity-50"
                    >
                      Save
                    </button>
                  </div>
                </div>
              )}

              <div className="space-y-2">
                {selectedEvents.map((event) => (
                  <button
                    key={event.id}
                    onClick={() => setSelectedEventId(event.id)}
                    className="w-full text-left p-3 bg-white/5 rounded-lg hover:bg-white/10 transition-colors"
                  >
                    <div className="font-medium text-[var(--color-text)]">{event.title}</div>
                    {event.time && (
                      <div className="text-sm text-[var(--color-text-muted)]">
                        {event.time}
                        {event.endTime && ` - ${event.endTime}`}
                      </div>
                    )}
                    {event.description && (
                      <div className="text-sm text-[var(--color-text-muted)] mt-1 line-clamp-2">
                        {event.description}
                      </div>
                    )}
                    {event.journalId && (
                      <div className="text-xs text-[var(--color-accent)] mt-1">Has linked journal</div>
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

      {/* Event Details Modal */}
      {selectedEventId && (
        <EventDetails eventId={selectedEventId} onClose={() => setSelectedEventId(null)} />
      )}
    </div>
  );
}
