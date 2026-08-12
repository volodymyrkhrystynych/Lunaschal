// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { DayEventLayer, type LaidOutEvent } from './DayEventLayer';
import type { CalendarEvent } from '../../hooks/api';
import {
  EVENT_LINE_WIDTH_PX,
  MIN_LINE_LENGTH_PX,
  RESIZE_CAP_PX,
} from '@/lib/calendarDayLayout';

const calendarEvent = (
  overrides: Partial<CalendarEvent> = {}
): CalendarEvent => ({
  id: 'ev-1',
  title: 'Gym',
  description: null,
  date: '2026-07-08',
  time: '09:00',
  endTime: '10:00',
  allDay: false,
  tags: null,
  journalId: null,
  createdAt: '2026-07-08T09:00:00',
  repeatFreq: null,
  repeatInterval: null,
  repeatByweekday: null,
  repeatUntil: null,
  categoryTags: null,
  classifiedAt: null,
  classificationError: null,
  ...overrides,
});

const laidOut = (overrides: Partial<LaidOutEvent> = {}): LaidOutEvent => ({
  event: calendarEvent(),
  range: { startMinutes: 540, endMinutes: 600 }, // 09:00-10:00
  depth: 0,
  ...overrides,
});

function renderLayer(events: LaidOutEvent[]) {
  const onOpenEvent = vi.fn();
  const onCommit = vi.fn();
  const onTranscribed = vi.fn();
  const queryClient = new QueryClient();
  const view = render(
    <QueryClientProvider client={queryClient}>
      <DayEventLayer
        events={events}
        pxPerMinute={1}
        onOpenEvent={onOpenEvent}
        onCommit={onCommit}
        onTranscribed={onTranscribed}
      />
    </QueryClientProvider>
  );
  return { ...view, onOpenEvent, onCommit, onTranscribed };
}

// Only clientY drives the drag math here (a vertical-only timeline), so the
// helper only needs to vary that.
const pointerAt = (y: number) => ({
  clientY: y,
  clientX: 0,
  pointerId: 1,
  bubbles: true,
});

beforeEach(() => {
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
});

describe('tap vs drag', () => {
  it('opens the event on a tap (pointer barely moves)', () => {
    const { onOpenEvent, onCommit } = renderLayer([laidOut()]);
    const line = screen.getByTestId('calendar-event-line');
    fireEvent.pointerDown(line, pointerAt(100));
    fireEvent.pointerUp(line, pointerAt(102)); // 2px of jitter, under the slop
    expect(onOpenEvent).toHaveBeenCalledTimes(1);
    expect(onOpenEvent.mock.calls[0][0]).toMatchObject({ id: 'ev-1' });
    expect(onCommit).not.toHaveBeenCalled();
  });

  it('does not open the event once the pointer has moved past the tap threshold', () => {
    const { onOpenEvent, onCommit } = renderLayer([laidOut()]);
    const line = screen.getByTestId('calendar-event-line');
    fireEvent.pointerDown(line, pointerAt(100));
    fireEvent.pointerMove(line, pointerAt(140));
    fireEvent.pointerUp(line, pointerAt(140));
    expect(onOpenEvent).not.toHaveBeenCalled();
    expect(onCommit).toHaveBeenCalledTimes(1);
  });
});

describe('dragging the line (move)', () => {
  it('shifts both start and end by the same snapped delta and commits once on release', () => {
    const { onCommit } = renderLayer([laidOut()]);
    const line = screen.getByTestId('calendar-event-line');
    fireEvent.pointerDown(line, pointerAt(100));
    fireEvent.pointerMove(line, pointerAt(140)); // +40px == +40min at pxPerMinute=1
    expect(onCommit).not.toHaveBeenCalled(); // nothing persists mid-drag

    fireEvent.pointerUp(line, pointerAt(140));
    expect(onCommit).toHaveBeenCalledTimes(1);
    const [id, range] = onCommit.mock.calls[0];
    expect(id).toBe('ev-1');
    expect(range).toEqual({ startMinutes: 580, endMinutes: 640 });
  });

  it('does not move a different event than the one dragged', () => {
    const events = [
      laidOut({
        event: calendarEvent({ id: 'a' }),
        range: { startMinutes: 0, endMinutes: 30 },
      }),
    ];
    const { onCommit } = renderLayer(events);
    const line = screen.getByTestId('calendar-event-line');
    fireEvent.pointerDown(line, pointerAt(0));
    fireEvent.pointerMove(line, pointerAt(50));
    fireEvent.pointerUp(line, pointerAt(50));
    expect(onCommit.mock.calls[0][0]).toBe('a');
  });

  it('moves and opens from the label row too', () => {
    // The label is the L's foot, hanging off the line's bottom end; it is a
    // grab target in its own right, since a 15-minute line is mostly cap.
    const { onCommit, onOpenEvent } = renderLayer([laidOut()]);
    const label = screen.getByText('Gym');
    fireEvent.pointerDown(label, pointerAt(600));
    fireEvent.pointerMove(label, pointerAt(660));
    fireEvent.pointerUp(label, pointerAt(660));
    expect(onCommit.mock.calls[0][1]).toEqual({
      startMinutes: 600,
      endMinutes: 660,
    });

    fireEvent.pointerDown(label, pointerAt(600));
    fireEvent.pointerUp(label, pointerAt(601));
    expect(onOpenEvent).toHaveBeenCalledTimes(1);
  });
});

describe('the shape that makes the timeline scrollable', () => {
  it('draws each event as a fixed-width line, not a block filling the row', () => {
    const { container } = renderLayer([laidOut()]);
    const line = screen.getByTestId('calendar-event-line');
    expect(line.style.width).toBe(`${EVENT_LINE_WIDTH_PX}px`);
    // Nothing in the block stretches to the right edge — a full-width block
    // is what made a thumb-scroll drag an event by accident.
    expect(container.querySelector('.right-0')).toBeNull();
  });

  it('claims touch only on the parts meant to be grabbed', () => {
    // The block's own root must not be touch-none, or it would swallow the
    // scroll gesture across the event's whole footprint again.
    renderLayer([laidOut()]);
    expect(screen.getByTestId('calendar-event-block').className).not.toContain(
      'touch-none'
    );
    expect(screen.getByTestId('calendar-event-line').className).toContain(
      'touch-none'
    );
  });

  it('lays overlapping events out side by side', () => {
    renderLayer([
      laidOut({ event: calendarEvent({ id: 'a' }), depth: 0 }),
      laidOut({ event: calendarEvent({ id: 'b', title: 'Call' }), depth: 1 }),
    ]);
    const [first, second] = screen.getAllByTestId('calendar-event-block');
    expect(first.style.left).toBe('0px');
    expect(parseInt(second.style.left, 10)).toBeGreaterThanOrEqual(
      EVENT_LINE_WIDTH_PX
    );
  });
});

describe('dragging the end cap (length)', () => {
  it('keeps the cap inside the line, so it cannot cover the next event', () => {
    // Back-to-back events share a lane; a cap straddling the end of one would
    // sit on top of the next one's start and steal its drags.
    renderLayer([laidOut()]);
    const handle = screen.getByTestId('calendar-event-resize-handle');
    expect(handle.className).toContain('bottom-0');
    expect(parseInt(handle.style.height, 10)).toBeLessThanOrEqual(
      RESIZE_CAP_PX
    );
  });

  it('leaves something to grab above the cap on the shortest event', () => {
    renderLayer([
      laidOut({ range: { startMinutes: 540, endMinutes: 555 } }), // 15 min
    ]);
    const block = screen.getByTestId('calendar-event-block');
    const handle = screen.getByTestId('calendar-event-resize-handle');
    expect(parseInt(block.style.height, 10)).toBe(MIN_LINE_LENGTH_PX);
    expect(parseInt(handle.style.height, 10)).toBeLessThan(MIN_LINE_LENGTH_PX);
  });

  it('extends the end without moving the start', () => {
    const { onCommit, onOpenEvent } = renderLayer([laidOut()]);
    const handle = screen.getByTestId('calendar-event-resize-handle');
    fireEvent.pointerDown(handle, pointerAt(600));
    fireEvent.pointerMove(handle, pointerAt(630));
    fireEvent.pointerUp(handle, pointerAt(630));

    expect(onCommit).toHaveBeenCalledTimes(1);
    const [, range] = onCommit.mock.calls[0];
    expect(range).toEqual({ startMinutes: 540, endMinutes: 630 });
    // The resize handle starting a drag must never read as a tap-to-open.
    expect(onOpenEvent).not.toHaveBeenCalled();
  });

  it('refuses to shrink the event below the minimum duration', () => {
    const { onCommit } = renderLayer([laidOut()]);
    const handle = screen.getByTestId('calendar-event-resize-handle');
    fireEvent.pointerDown(handle, pointerAt(600));
    fireEvent.pointerMove(handle, pointerAt(0)); // drag the handle way up past the start
    fireEvent.pointerUp(handle, pointerAt(0));

    const [, range] = onCommit.mock.calls[0];
    expect(range.endMinutes - range.startMinutes).toBeGreaterThanOrEqual(15);
  });
});
