// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { DayView } from './DayView';
import { api, type CalendarEvent, type SleepDay } from '../../hooks/api';

vi.mock('../../hooks/api', () => ({
  api: {
    calendar: {
      listByDate: vi.fn(),
      sleep: { get: vi.fn() },
      update: vi.fn(),
      moveOccurrence: vi.fn(),
      create: vi.fn(),
    },
  },
}));

const DATE = '2026-07-08';
const NEXT_DATE = '2026-07-09';
const at = (date: string, hhmm: string) =>
  new Date(`${date}T${hhmm}:00`).getTime() / 1000;

/** Both dates the timeline spans, answered from one map. */
function mockDays(byDate: Record<string, Partial<CalendarEvent>[]>) {
  vi.mocked(api.calendar.listByDate).mockImplementation(async d =>
    (byDate[d] ?? []).map(
      e => ({ allDay: false, isRecurring: false, ...e }) as CalendarEvent
    )
  );
}

const sleepDay = (overrides: Partial<SleepDay> = {}): SleepDay => ({
  date: DATE,
  wakeAt: null,
  sleepAt: null,
  wakeSource: null,
  sleepSource: null,
  previousSleepAt: null,
  nextWakeAt: null,
  ...overrides,
});

function renderDay(
  sleep: SleepDay = sleepDay(),
  byDate: Record<string, Partial<CalendarEvent>[]> = {}
) {
  vi.mocked(api.calendar.sleep.get).mockResolvedValue(sleep);
  mockDays(byDate);
  const onEditSleep = vi.fn();
  const onOpenEvent = vi.fn();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={client}>
      <DayView
        date={DATE}
        onOpenEvent={onOpenEvent}
        onEditSleep={onEditSleep}
      />
    </QueryClientProvider>
  );
  return { ...view, onEditSleep, onOpenEvent };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('sleep bands', () => {
  it('shades the morning up to the wake time and labels it', async () => {
    renderDay(sleepDay({ wakeAt: at(DATE, '07:20') }));
    const band = await screen.findByTestId('sleep-band-morning');
    // 1px per minute, and the timeline starts at 4am: 04:00 to 07:20 is 200px
    // tall, starting at the top.
    expect(band.style.top).toBe('0px');
    expect(band.style.height).toBe('200px');
    expect(band.textContent).toContain('woke 07:20');
    expect(screen.queryByTestId('sleep-band-evening')).toBeNull();
  });

  it('shades the evening from the bedtime down to the end of the day', async () => {
    renderDay(sleepDay({ sleepAt: at(DATE, '23:40') }));
    const band = await screen.findByTestId('sleep-band-evening');
    expect(band.style.top).toBe('1180px');
    expect(band.style.height).toBe('260px');
  });

  it('draws a past-midnight bedtime near the bottom instead of dropping it', async () => {
    // Under the old midnight-anchored grid this band belonged to the *next*
    // day's view and this one showed no evening sleep at all.
    renderDay(sleepDay({ sleepAt: at(NEXT_DATE, '01:30') }));
    const band = await screen.findByTestId('sleep-band-evening');
    expect(band.style.top).toBe('1290px');
    expect(band.style.height).toBe('150px');
  });

  it('draws nothing on a day with no sleep data', async () => {
    renderDay(sleepDay());
    await waitFor(() => expect(api.calendar.sleep.get).toHaveBeenCalled());
    expect(screen.queryByTestId('sleep-band-morning')).toBeNull();
    expect(screen.queryByTestId('sleep-band-evening')).toBeNull();
  });

  it('opens the editor when a band is tapped', async () => {
    const { onEditSleep } = renderDay(sleepDay({ wakeAt: at(DATE, '07:20') }));
    fireEvent.click(await screen.findByTestId('sleep-band-morning'));
    expect(onEditSleep).toHaveBeenCalledTimes(1);
  });

  it('leaves the event layer transparent to taps so a band stays reachable', async () => {
    // The layer covers the full width of every row; without this a band could
    // only be tapped in the 48px hour gutter. Each EventBlock turns hit-testing
    // back on for its own footprint (see EventBlock's root className).
    const { container } = renderDay(sleepDay({ wakeAt: at(DATE, '07:20') }));
    await screen.findByTestId('sleep-band-morning');
    // The layer wrapper specifically (left-12), not just anything transparent.
    expect(
      container.querySelector('.left-12.pointer-events-none')
    ).not.toBeNull();
  });
});

const pointerAt = (y: number) => ({ clientY: y, clientX: 0, pointerId: 1 });

const blockFor = (title: string) =>
  screen
    .getByText(title)
    .closest('[data-testid="calendar-event-block"]') as HTMLElement | null;

/** Grab an event's line and drag it `deltaPx` down the timeline. */
function dragBy(title: string, deltaPx: number) {
  const line = blockFor(title)!.querySelector(
    '[data-testid="calendar-event-line"]'
  )!;
  fireEvent.pointerDown(line, pointerAt(0));
  fireEvent.pointerMove(line, pointerAt(deltaPx));
  fireEvent.pointerUp(line, pointerAt(deltaPx));
}

describe('the 4am-to-4am timeline', () => {
  it('places a daytime event by its distance below 4am', async () => {
    renderDay(sleepDay(), {
      [DATE]: [
        {
          id: 'a',
          title: 'Standup',
          date: DATE,
          time: '09:30',
          endTime: '10:00',
        },
      ],
    });
    expect((await screen.findByText('Standup')).closest('div')).toBeTruthy();
    // 09:30 is 5h30 past the 4am top, at 1px per minute.
    expect(blockFor('Standup')!.style.top).toBe('330px');
  });

  it('draws the small hours of the next date at the bottom of this day', async () => {
    // The behaviour the change exists for: a 01:30 event is the tail of this
    // day even though it is filed under tomorrow's date.
    renderDay(sleepDay(), {
      [NEXT_DATE]: [
        {
          id: 'b',
          title: 'Late call',
          date: NEXT_DATE,
          time: '01:30',
          endTime: '02:00',
        },
      ],
    });
    await screen.findByText('Late call');
    expect(blockFor('Late call')!.style.top).toBe('1290px');
  });

  it('leaves this date own small hours to the previous day view', async () => {
    // 01:30 on DATE belongs to the day that began on the 7th, and is drawn
    // there — showing it here too would put one event on two days.
    renderDay(sleepDay(), {
      [DATE]: [
        { id: 'c', title: 'Yesterday tail', date: DATE, time: '01:30' },
        { id: 'd', title: 'Breakfast', date: DATE, time: '08:00' },
      ],
    });
    await screen.findByText('Breakfast');
    expect(screen.queryByText('Yesterday tail')).toBeNull();
  });

  it('moves an event dragged past midnight onto the next calendar date', async () => {
    vi.mocked(api.calendar.update).mockResolvedValue({ success: true });
    renderDay(sleepDay(), {
      [DATE]: [
        {
          id: 'e',
          title: 'Night owl',
          date: DATE,
          time: '23:00',
          endTime: '23:30',
        },
      ],
    });
    await screen.findByText('Night owl');
    dragBy('Night owl', 120); // 23:00 -> 01:00, across the date line

    await waitFor(() => expect(api.calendar.update).toHaveBeenCalled());
    expect(api.calendar.update).toHaveBeenCalledWith('e', {
      date: NEXT_DATE,
      time: '01:00',
      endTime: '01:30',
    });
  });

  it('retimes without rewriting the date when the drag stays on one side', async () => {
    vi.mocked(api.calendar.update).mockResolvedValue({ success: true });
    renderDay(sleepDay(), {
      [DATE]: [
        {
          id: 'f',
          title: 'Standup',
          date: DATE,
          time: '09:00',
          endTime: '09:30',
        },
      ],
    });
    await screen.findByText('Standup');
    dragBy('Standup', 60);

    await waitFor(() => expect(api.calendar.update).toHaveBeenCalled());
    expect(api.calendar.update).toHaveBeenCalledWith('f', {
      time: '10:00',
      endTime: '10:30',
    });
  });

  it('sends an after-midnight event back to the day it is filed under', async () => {
    // Dragging the 01:30 event *up* past midnight returns it to DATE.
    vi.mocked(api.calendar.update).mockResolvedValue({ success: true });
    renderDay(sleepDay(), {
      [NEXT_DATE]: [
        {
          id: 'g',
          title: 'Late call',
          date: NEXT_DATE,
          time: '01:30',
          endTime: '02:00',
        },
      ],
    });
    await screen.findByText('Late call');
    dragBy('Late call', -180); // 01:30 -> 22:30 the previous evening

    await waitFor(() => expect(api.calendar.update).toHaveBeenCalled());
    expect(api.calendar.update).toHaveBeenCalledWith('g', {
      date: DATE,
      time: '22:30',
      endTime: '23:00',
    });
  });

  it('labels midnight in the middle of the grid rather than the top', async () => {
    renderDay();
    await waitFor(() => expect(api.calendar.sleep.get).toHaveBeenCalled());
    const midnight = screen.getByText('12am');
    expect(midnight.closest('div')!.parentElement!.style.top).toBe('1200px');
    // 4am is the top edge, where a label would be clipped in half.
    expect(screen.queryByText('4am')).toBeNull();
  });
});
