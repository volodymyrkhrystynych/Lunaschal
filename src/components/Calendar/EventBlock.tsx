import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { CalendarEvent } from '../../hooks/api';
import { api } from '../../hooks/api';
import { useRecorder } from '../../hooks/useRecorder';
import {
  categoryStripeBackground,
  parseCategoryTags,
} from '@/lib/calendarCategories';
import { EVENT_LINE_WIDTH_PX, RESIZE_CAP_PX } from '@/lib/calendarDayLayout';
import { eventTimeLabel } from '@/lib/calendar';

interface EventBlockProps {
  event: CalendarEvent;
  top: number;
  /** Length of the line — the event's duration in px, floored so the resize
   * cap always fits (see eventLineLengthPx). */
  length: number;
  laneOffsetPx: number;
  zIndex: number;
  onBodyPointerDown: (e: React.PointerEvent) => void;
  onHandlePointerDown: (e: React.PointerEvent) => void;
  onPointerMove: (e: React.PointerEvent) => void;
  onPointerUp: (e: React.PointerEvent) => void;
  onPointerCancel: (e: React.PointerEvent) => void;
  onTranscribed: () => void;
}

/**
 * One event on the day timeline, drawn as a thin vertical line with its label
 * hanging off the bottom end — an L. The shape is the interaction: a
 * full-width block covered the whole scroll surface, so a thumb reaching to
 * scroll landed on an event and dragged it instead. Only the line, its label
 * row and its end cap claim touch now; everything else in the row scrolls.
 */
export function EventBlock({
  event,
  top,
  length,
  laneOffsetPx,
  zIndex,
  onBodyPointerDown,
  onHandlePointerDown,
  onPointerMove,
  onPointerUp,
  onPointerCancel,
  onTranscribed,
}: EventBlockProps) {
  const queryClient = useQueryClient();
  const categories = parseCategoryTags(event.categoryTags);
  const pending = !!event.description && !event.classifiedAt;

  const transcribe = useMutation({
    mutationFn: (text: string) => api.calendar.transcribe(event.id, text),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['calendar'] });
      onTranscribed();
    },
  });

  const recorder = useRecorder(text => transcribe.mutate(text));

  const micLabel =
    recorder.status === 'recording'
      ? '⏹'
      : recorder.status === 'transcribing' || transcribe.isPending
        ? '…'
        : '🎤';

  // Drag-to-move and tap-to-open are the same gesture until the pointer
  // travels, so the line and the label row share one set of handlers (the
  // layer disambiguates on release).
  const dragHandlers = {
    onPointerDown: onBodyPointerDown,
    onPointerMove,
    onPointerUp,
    onPointerCancel,
  };

  return (
    <div
      data-testid="calendar-event-block"
      className="absolute select-none"
      style={{ top, height: length, left: laneOffsetPx, zIndex }}
    >
      <div
        data-testid="calendar-event-line"
        className="absolute top-0 bottom-0 left-0 rounded-full touch-none"
        style={{
          width: EVENT_LINE_WIDTH_PX,
          // One vertical stripe per category tag across a constant width —
          // the day view's counterpart to the concentric rings the Journal
          // feed's event groups use (see categoryStripeBackground). No
          // categories yet (untagged, or still awaiting classification)
          // falls back to a flat neutral fill.
          backgroundImage: categories.length
            ? categoryStripeBackground(categories)
            : undefined,
          backgroundColor: categories.length
            ? undefined
            : pending
              ? 'var(--color-text-muted)'
              : 'rgba(255,255,255,0.25)',
        }}
        {...dragHandlers}
      />

      {/* The L's foot: mic + title + time, overflowing to the right of the
          line. Truncated rather than wrapped so a long title can't push the
          row taller than the label band it sits in. */}
      <div
        className="absolute bottom-0 flex items-center gap-1 min-w-0 max-w-[60vw] touch-none"
        style={{ left: EVENT_LINE_WIDTH_PX + 4 }}
        {...dragHandlers}
      >
        <button
          type="button"
          aria-label="Record for this event"
          data-testid="calendar-event-mic"
          onPointerDown={e => e.stopPropagation()}
          onClick={e => {
            e.stopPropagation();
            if (recorder.status === 'idle') recorder.start();
            else if (recorder.status === 'recording') recorder.stop();
          }}
          className="shrink-0 w-7 h-7 flex items-center justify-center rounded-full bg-black/30 text-xs text-[var(--color-text)] hover:bg-black/50"
        >
          {micLabel}
        </button>
        <div className="min-w-0 leading-tight">
          <div className="text-xs font-medium text-[var(--color-text)] truncate">
            {event.title}
          </div>
          {eventTimeLabel(event) && (
            <div className="text-[10px] text-[var(--color-text-muted)] truncate">
              {eventTimeLabel(event)}
            </div>
          )}
        </div>
      </div>

      {/* Grab zone for the length, sitting inside the bottom of the line so it
          never covers the next event's start. */}
      <div
        data-testid="calendar-event-resize-handle"
        onPointerDown={onHandlePointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerCancel}
        className="absolute bottom-0 left-0 flex items-end justify-center touch-none cursor-ns-resize"
        style={{
          width: EVENT_LINE_WIDTH_PX,
          height: Math.min(RESIZE_CAP_PX, length),
        }}
      >
        <div className="mb-1 w-4 h-1 rounded-full bg-black/50" />
      </div>
    </div>
  );
}
