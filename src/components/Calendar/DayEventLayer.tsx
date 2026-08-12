import { useState } from 'react';
import type { CalendarEvent } from '../../hooks/api';
import {
  eventHeightPx,
  eventTopPx,
  moveEventByMinutes,
  pxDeltaToMinutes,
  resizeEventEndByMinutes,
  OVERLAP_INSET_PX,
  type EventTimeRange,
} from '@/lib/calendarDayLayout';
import { EventBlock } from './EventBlock';

/** Pointer movement below this, start-to-end, reads as a tap rather than a
 * drag — the same disambiguation any touch calendar needs, since a tap and
 * the start of a drag look identical until the pointer actually moves. */
const TAP_SLOP_PX = 6;

export interface LaidOutEvent {
  event: CalendarEvent;
  range: EventTimeRange;
  depth: number;
}

interface DragState {
  id: string;
  mode: 'move' | 'resize';
  pointerId: number;
  startClientY: number;
  moved: boolean;
  range: EventTimeRange;
}

interface DayEventLayerProps {
  events: LaidOutEvent[];
  pxPerMinute: number;
  onOpenEvent: (event: CalendarEvent) => void;
  onCommit: (eventId: string, range: EventTimeRange) => void;
  onTranscribed: (eventId: string) => void;
}

/**
 * Renders one day's event blocks and drives their drag-to-move /
 * drag-to-resize-from-bottom-edge interaction.
 *
 * Adapted from Paper/PaperImageLayer.tsx's technique (pointer capture, a
 * mode/last/box drag state, live preview via a pure geometry function,
 * commit on pointerUp) rather than copied verbatim: Paper's images are
 * canvas-drawn, so a single transparent overlay on top does the hit-testing.
 * Calendar events are real DOM nodes with their own interactive children (the
 * transcribe mic button), so each EventBlock's own root receives the pointer
 * events directly — pointer capture still redirects every subsequent move/up
 * for that pointerId to the element that started the drag, so only one
 * block's handlers ever fire during a given drag regardless of where the
 * finger travels.
 */
export function DayEventLayer({
  events,
  pxPerMinute,
  onOpenEvent,
  onCommit,
  onTranscribed,
}: DayEventLayerProps) {
  const [drag, setDrag] = useState<DragState | null>(null);

  const rangeFor = (laid: LaidOutEvent): EventTimeRange =>
    drag && drag.id === laid.event.id ? drag.range : laid.range;

  const startDrag = (
    e: React.PointerEvent,
    laid: LaidOutEvent,
    mode: 'move' | 'resize'
  ) => {
    e.stopPropagation();
    setDrag({
      id: laid.event.id,
      mode,
      pointerId: e.pointerId,
      startClientY: e.clientY,
      moved: false,
      range: laid.range,
    });
    try {
      (e.target as Element).setPointerCapture?.(e.pointerId);
    } catch {
      // Pointer capture is a nicety; a drag still tracks without it.
    }
  };

  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag || e.pointerId !== drag.pointerId) return;
    const deltaPx = e.clientY - drag.startClientY;
    const laid = events.find(l => l.event.id === drag.id);
    if (!laid) return;
    const deltaMinutes = pxDeltaToMinutes(deltaPx, pxPerMinute);
    const range =
      drag.mode === 'move'
        ? moveEventByMinutes(laid.range, deltaMinutes)
        : resizeEventEndByMinutes(laid.range, deltaMinutes);
    setDrag({
      ...drag,
      range,
      moved: drag.moved || Math.abs(deltaPx) > TAP_SLOP_PX,
    });
  };

  const endDrag = () => {
    if (!drag) return;
    const laid = events.find(l => l.event.id === drag.id);
    if (laid) {
      if (drag.moved) onCommit(drag.id, drag.range);
      else onOpenEvent(laid.event);
    }
    setDrag(null);
  };

  return (
    <>
      {events.map(laid => {
        const range = rangeFor(laid);
        return (
          <EventBlock
            key={laid.event.id}
            event={laid.event}
            top={eventTopPx(range.startMinutes, pxPerMinute)}
            height={eventHeightPx(
              range.startMinutes,
              range.endMinutes,
              pxPerMinute
            )}
            insetPx={laid.depth * OVERLAP_INSET_PX}
            zIndex={laid.depth + 1}
            onBodyPointerDown={e => startDrag(e, laid, 'move')}
            onHandlePointerDown={e => startDrag(e, laid, 'resize')}
            onPointerMove={onPointerMove}
            onPointerUp={endDrag}
            onPointerCancel={endDrag}
            onTranscribed={() => onTranscribed(laid.event.id)}
          />
        );
      })}
    </>
  );
}
