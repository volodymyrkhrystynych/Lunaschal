import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import {
  EMPTY_REPEAT,
  eventTimeLabel,
  parseEventTags,
  repeatDraftFromEvent,
  repeatDraftToPayload,
  repeatLabel,
  type RepeatDraft,
} from '@/lib/calendar';
import { parseTagsInput } from '@/lib/tags';
import {
  CATEGORY_LABELS,
  CATEGORY_COLORS,
  parseCategoryTags,
  type EventCategory,
} from '@/lib/calendarCategories';
import { CategoryTagPicker, RepeatFields } from './EventFormFields';

export function EventDetails({
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
    // Comma-separated text while editing; the stored JSON array is parsed on
    // the way in and split again on save.
    tags: '',
    // The grouping categories (leisure/work/exercise/family/outside/indoors)
    // that make the Journal feed draw this event's border around entries in
    // its time window — normally AI-assigned from a transcribed description,
    // editable here so grouping doesn't have to wait on that.
    categoryTags: [] as EventCategory[],
    // The recurrence rule, editable here as well as at creation. Without it a
    // mistyped repeat could only be corrected by deleting the series and
    // typing the whole event again.
    repeat: EMPTY_REPEAT as RepeatDraft,
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
        tags: parseTagsInput(draft.tags),
        categoryTags: draft.categoryTags,
        ...repeatDraftToPayload(draft.repeat),
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
      tags: parseEventTags(event.tags).join(', '),
      categoryTags: parseCategoryTags(event.categoryTags),
      repeat: repeatDraftFromEvent(event),
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
            <input
              type="text"
              aria-label="Tags"
              value={draft.tags}
              onChange={e => setDraft({ ...draft, tags: e.target.value })}
              placeholder="Tags (comma-separated)"
              className="w-full bg-transparent text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border-b border-white/10 pb-2 focus:outline-none"
            />
            <RepeatFields
              value={draft.repeat}
              onChange={repeat => setDraft({ ...draft, repeat })}
              anchorDate={shownDate}
            />
            <CategoryTagPicker
              value={draft.categoryTags}
              onChange={categoryTags => setDraft({ ...draft, categoryTags })}
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
          <div className="tag-row flex gap-1 mb-2">
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

        {mode === 'view' &&
          parseCategoryTags(event.categoryTags).length > 0 && (
            <div className="tag-row flex gap-1 mb-4">
              {parseCategoryTags(event.categoryTags).map(category => (
                <span
                  key={category}
                  className="px-2 py-0.5 text-xs rounded"
                  style={{
                    color: CATEGORY_COLORS[category],
                    border: `1px solid ${CATEGORY_COLORS[category]}`,
                  }}
                >
                  {CATEGORY_LABELS[category]}
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
