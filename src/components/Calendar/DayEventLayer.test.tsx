// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { DayEventLayer, type LaidOutEvent } from './DayEventLayer';
import type { CalendarEvent } from '../../hooks/api';

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
    const block = screen.getByTestId('calendar-event-block');
    fireEvent.pointerDown(block, pointerAt(100));
    fireEvent.pointerUp(block, pointerAt(102)); // 2px of jitter, under the slop
    expect(onOpenEvent).toHaveBeenCalledTimes(1);
    expect(onOpenEvent.mock.calls[0][0]).toMatchObject({ id: 'ev-1' });
    expect(onCommit).not.toHaveBeenCalled();
  });

  it('does not open the event once the pointer has moved past the tap threshold', () => {
    const { onOpenEvent, onCommit } = renderLayer([laidOut()]);
    const block = screen.getByTestId('calendar-event-block');
    fireEvent.pointerDown(block, pointerAt(100));
    fireEvent.pointerMove(block, pointerAt(140));
    fireEvent.pointerUp(block, pointerAt(140));
    expect(onOpenEvent).not.toHaveBeenCalled();
    expect(onCommit).toHaveBeenCalledTimes(1);
  });
});

describe('dragging the body (move)', () => {
  it('shifts both start and end by the same snapped delta and commits once on release', () => {
    const { onCommit } = renderLayer([laidOut()]);
    const block = screen.getByTestId('calendar-event-block');
    fireEvent.pointerDown(block, pointerAt(100));
    fireEvent.pointerMove(block, pointerAt(140)); // +40px == +40min at pxPerMinute=1
    expect(onCommit).not.toHaveBeenCalled(); // nothing persists mid-drag

    fireEvent.pointerUp(block, pointerAt(140));
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
    const block = screen.getByTestId('calendar-event-block');
    fireEvent.pointerDown(block, pointerAt(0));
    fireEvent.pointerMove(block, pointerAt(50));
    fireEvent.pointerUp(block, pointerAt(50));
    expect(onCommit.mock.calls[0][0]).toBe('a');
  });
});

describe('dragging the resize handle', () => {
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
