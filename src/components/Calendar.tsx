import { useState } from 'react';
import { trpc } from '../hooks/trpc';

export function Calendar() {
  const [currentDate, setCurrentDate] = useState(new Date());
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [newEvent, setNewEvent] = useState({ title: '', description: '', time: '' });
  const [showNewEvent, setShowNewEvent] = useState(false);

  const utils = trpc.useUtils();

  // Get start and end of current month
  const startOfMonth = new Date(currentDate.getFullYear(), currentDate.getMonth(), 1);
  const endOfMonth = new Date(currentDate.getFullYear(), currentDate.getMonth() + 1, 0);

  const formatDateISO = (date: Date) => date.toISOString().split('T')[0];

  const { data: events } = trpc.calendar.listByRange.useQuery({
    startDate: formatDateISO(startOfMonth),
    endDate: formatDateISO(endOfMonth),
  });

  const createEvent = trpc.calendar.create.useMutation({
    onSuccess: () => {
      utils.calendar.listByRange.invalidate();
      setNewEvent({ title: '', description: '', time: '' });
      setShowNewEvent(false);
    },
  });

  const deleteEvent = trpc.calendar.delete.useMutation({
    onSuccess: () => {
      utils.calendar.listByRange.invalidate();
    },
  });

  const handleCreate = () => {
    if (!newEvent.title.trim() || !selectedDate) return;
    createEvent.mutate({
      title: newEvent.title,
      description: newEvent.description || undefined,
      date: selectedDate,
      time: newEvent.time || undefined,
    });
  };

  // Calendar grid helpers
  const daysInMonth = endOfMonth.getDate();
  const firstDayOfMonth = startOfMonth.getDay();
  const monthName = currentDate.toLocaleString('default', { month: 'long', year: 'numeric' });

  const prevMonth = () => {
    setCurrentDate(new Date(currentDate.getFullYear(), currentDate.getMonth() - 1, 1));
  };

  const nextMonth = () => {
    setCurrentDate(new Date(currentDate.getFullYear(), currentDate.getMonth() + 1, 1));
  };

  const getEventsForDate = (date: string) => {
    return events?.filter((e) => e.date === date) || [];
  };

  const days = [];
  for (let i = 0; i < firstDayOfMonth; i++) {
    days.push(null);
  }
  for (let i = 1; i <= daysInMonth; i++) {
    days.push(i);
  }

  const selectedEvents = selectedDate ? getEventsForDate(selectedDate) : [];

  return (
    <div className="flex-1 flex flex-col p-4 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-semibold text-[var(--color-text)]">Calendar</h1>
      </div>

      <div className="flex-1 flex gap-4 overflow-hidden">
        {/* Calendar Grid */}
        <div className="flex-1">
          {/* Month Navigation */}
          <div className="flex items-center justify-between mb-4">
            <button
              onClick={prevMonth}
              className="p-2 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            >
              ←
            </button>
            <h2 className="text-lg font-medium text-[var(--color-text)]">{monthName}</h2>
            <button
              onClick={nextMonth}
              className="p-2 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            >
              →
            </button>
          </div>

          {/* Day Headers */}
          <div className="grid grid-cols-7 gap-1 mb-2">
            {['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map((day) => (
              <div
                key={day}
                className="text-center text-sm text-[var(--color-text-muted)] py-2"
              >
                {day}
              </div>
            ))}
          </div>

          {/* Calendar Days */}
          <div className="grid grid-cols-7 gap-1">
            {days.map((day, index) => {
              if (day === null) {
                return <div key={`empty-${index}`} className="aspect-square" />;
              }

              const dateStr = formatDateISO(
                new Date(currentDate.getFullYear(), currentDate.getMonth(), day)
              );
              const dayEvents = getEventsForDate(dateStr);
              const isSelected = selectedDate === dateStr;
              const isToday = dateStr === formatDateISO(new Date());

              return (
                <button
                  key={day}
                  onClick={() => setSelectedDate(dateStr)}
                  className={`aspect-square p-1 rounded-lg border transition-colors ${
                    isSelected
                      ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/20'
                      : 'border-transparent hover:border-white/20'
                  } ${isToday ? 'bg-[var(--color-surface)]' : ''}`}
                >
                  <div
                    className={`text-sm ${
                      isToday ? 'text-[var(--color-primary)] font-semibold' : 'text-[var(--color-text)]'
                    }`}
                  >
                    {day}
                  </div>
                  {dayEvents.length > 0 && (
                    <div className="mt-1 flex gap-0.5 flex-wrap">
                      {dayEvents.slice(0, 3).map((e) => (
                        <div
                          key={e.id}
                          className="w-1.5 h-1.5 rounded-full bg-[var(--color-accent)]"
                        />
                      ))}
                    </div>
                  )}
                </button>
              );
            })}
          </div>
        </div>

        {/* Selected Date Events */}
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
                  <input
                    type="time"
                    value={newEvent.time}
                    onChange={(e) => setNewEvent({ ...newEvent, time: e.target.value })}
                    className="w-full bg-transparent text-[var(--color-text)] border-b border-white/10 pb-2 mb-2 focus:outline-none"
                  />
                  <textarea
                    value={newEvent.description}
                    onChange={(e) => setNewEvent({ ...newEvent, description: e.target.value })}
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
                  <div
                    key={event.id}
                    className="p-3 bg-white/5 rounded-lg group"
                  >
                    <div className="flex items-start justify-between">
                      <div>
                        <div className="font-medium text-[var(--color-text)]">{event.title}</div>
                        {event.time && (
                          <div className="text-sm text-[var(--color-text-muted)]">{event.time}</div>
                        )}
                        {event.description && (
                          <div className="text-sm text-[var(--color-text-muted)] mt-1">
                            {event.description}
                          </div>
                        )}
                      </div>
                      <button
                        onClick={() => deleteEvent.mutate({ id: event.id })}
                        className="text-red-400 hover:text-red-300 opacity-0 group-hover:opacity-100 transition-opacity"
                      >
                        ×
                      </button>
                    </div>
                  </div>
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
    </div>
  );
}
