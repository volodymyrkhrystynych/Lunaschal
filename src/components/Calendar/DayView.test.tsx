// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { DayView } from './DayView';
import { api, type SleepDay } from '../../hooks/api';

vi.mock('../../hooks/api', () => ({
  api: {
    calendar: {
      listByDate: vi.fn(),
      sleep: { get: vi.fn() },
    },
  },
}));

const DATE = '2026-07-08';
const at = (date: string, hhmm: string) =>
  new Date(`${date}T${hhmm}:00`).getTime() / 1000;

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

function renderDay(sleep: SleepDay) {
  vi.mocked(api.calendar.listByDate).mockResolvedValue([]);
  vi.mocked(api.calendar.sleep.get).mockResolvedValue(sleep);
  const onEditSleep = vi.fn();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={client}>
      <DayView date={DATE} onOpenEvent={vi.fn()} onEditSleep={onEditSleep} />
    </QueryClientProvider>
  );
  return { ...view, onEditSleep };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('sleep bands', () => {
  it('shades the morning up to the wake time and labels it', async () => {
    renderDay(sleepDay({ wakeAt: at(DATE, '07:20') }));
    const band = await screen.findByTestId('sleep-band-morning');
    // 1px per minute: midnight to 07:20 is 440px tall, starting at the top.
    expect(band.style.top).toBe('0px');
    expect(band.style.height).toBe('440px');
    expect(band.textContent).toContain('woke 07:20');
    expect(screen.queryByTestId('sleep-band-evening')).toBeNull();
  });

  it('shades the evening from the bedtime down to midnight', async () => {
    renderDay(sleepDay({ sleepAt: at(DATE, '23:40') }));
    const band = await screen.findByTestId('sleep-band-evening');
    expect(band.style.top).toBe('1420px');
    expect(band.style.height).toBe('20px');
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
