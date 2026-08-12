import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { CalendarEvent } from '../../hooks/api';
import { api } from '../../hooks/api';
import { useRecorder } from '../../hooks/useRecorder';
import {
  categoryRingBoxShadow,
  parseCategoryTags,
} from '@/lib/calendarCategories';
import { eventTimeLabel } from '@/lib/calendar';

/** Touch target for the resize handle, matching the app-wide 44px minimum
 * (see HANDLE_PX in Paper/PaperImageLayer.tsx). */
export const RESIZE_HANDLE_PX = 44;

interface EventBlockProps {
  event: CalendarEvent;
  top: number;
  height: number;
  insetPx: number;
  zIndex: number;
  onBodyPointerDown: (e: React.PointerEvent) => void;
  onHandlePointerDown: (e: React.PointerEvent) => void;
  onPointerMove: (e: React.PointerEvent) => void;
  onPointerUp: (e: React.PointerEvent) => void;
  onPointerCancel: (e: React.PointerEvent) => void;
  onTranscribed: () => void;
}

export function EventBlock({
  event,
  top,
  height,
  insetPx,
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

  return (
    <div
      data-testid="calendar-event-block"
      className="absolute left-0 right-0 select-none touch-none"
      style={{ top, height, zIndex, left: insetPx, right: insetPx }}
      onPointerDown={onBodyPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerCancel}
    >
      <div
        className="relative w-full h-full rounded-md bg-[var(--color-surface)] overflow-hidden flex flex-col justify-end"
        style={{
          // One concentric ring per category tag (up to 3) — the same
          // "stack borders, don't pick one" approach overlapping events use
          // (see DayEventLayer's insetPx), rendered as a single box-shadow
          // stack instead of nested elements. No categories yet (untagged or
          // still awaiting classification) falls back to a plain outline.
          boxShadow: categories.length
            ? categoryRingBoxShadow(categories)
            : `0 0 0 2px ${pending ? 'var(--color-text-muted)' : 'rgba(255,255,255,0.2)'}`,
        }}
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
          className="absolute top-1 right-1 w-7 h-7 flex items-center justify-center rounded-full bg-black/30 text-xs text-[var(--color-text)] hover:bg-black/50"
        >
          {micLabel}
        </button>

        <div className="px-2 pb-1 min-w-0">
          <div className="text-xs font-medium text-[var(--color-text)] truncate">
            {event.title}
          </div>
          {eventTimeLabel(event) && (
            <div className="text-[10px] text-[var(--color-text-muted)] truncate">
              {eventTimeLabel(event)}
            </div>
          )}
        </div>

        <div
          data-testid="calendar-event-resize-handle"
          onPointerDown={onHandlePointerDown}
          className="absolute left-0 right-0 flex items-center justify-center touch-none cursor-ns-resize"
          style={{ bottom: -RESIZE_HANDLE_PX / 2, height: RESIZE_HANDLE_PX }}
        >
          <div className="w-8 h-1 rounded-full bg-white/40" />
        </div>
      </div>
    </div>
  );
}
