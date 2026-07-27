// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, type CalendarEvent } from '../hooks/api';
import { Calendar } from './Calendar';

vi.mock('../hooks/api', () => ({
  api: {
    calendar: {
      listByRange: vi.fn(),
      listByWeek: vi.fn(),
      get: vi.fn(),
      findRelatedJournals: vi.fn(),
      create: vi.fn(),
      update: vi.fn(),
      delete: vi.fn(),
      skipOccurrence: vi.fn(),
      endSeries: vi.fn(),
      updateFrom: vi.fn(),
      linkJournal: vi.fn(),
      unlinkJournal: vi.fn(),
    },
  },
}));

const event = (over: Partial<CalendarEvent> = {}): CalendarEvent => ({
  id: 'e1',
  title: 'Work',
  description: null,
  date: '2026-02-04',
  time: '09:00',
  endTime: '17:00',
  tags: null,
  journalId: null,
  createdAt: '',
  repeatFreq: null,
  repeatInterval: null,
  repeatByweekday: null,
  repeatUntil: null,
  ...over,
});

// February 2026 starts on a Sunday and has 28 days — exactly four weeks, the
// month most likely to expose a grid that sizes itself off its content.
const FEB_2026 = new Date(2026, 1, 15, 12, 0);

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(FEB_2026);
  // Desktop layout: useIsMobile reads matchMedia, absent in jsdom.
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
  vi.mocked(api.calendar.listByRange).mockResolvedValue([]);
  vi.mocked(api.calendar.listByWeek).mockResolvedValue([]);
  vi.mocked(api.calendar.findRelatedJournals).mockResolvedValue([]);
  vi.mocked(api.calendar.create).mockResolvedValue({ id: 'new' });
  vi.mocked(api.calendar.skipOccurrence).mockResolvedValue({ success: true });
  vi.mocked(api.calendar.delete).mockResolvedValue({ success: true });
  vi.mocked(api.calendar.update).mockResolvedValue({ success: true });
  vi.mocked(api.calendar.endSeries).mockResolvedValue({
    success: true,
    deleted: false,
  });
  vi.mocked(api.calendar.updateFrom).mockResolvedValue({
    id: 'new',
    split: true,
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

function renderCalendar() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <Calendar />
    </QueryClientProvider>
  );
}

describe('month grid layout', () => {
  it('renders six rows even for a four-week month', async () => {
    const { container } = renderCalendar();
    await waitFor(() =>
      expect(
        container.querySelectorAll('[data-testid="month-cell"]')
      ).toHaveLength(42)
    );
  });

  it('sizes rows off the available height, not the grid width', async () => {
    // aspect-square cells overflow a short window and the clipped weeks are
    // unreachable — the bug this view is fixing.
    const { container } = renderCalendar();
    const grid = await screen.findByTestId('month-grid');
    expect(grid.className).toContain('grid-rows-6');
    expect(grid.className).toContain('min-h-0');
    expect(container.innerHTML).not.toContain('aspect-square');
  });

  it('fetches the whole visible grid, not just the month', async () => {
    renderCalendar();
    // Feb 2026 is a clean 4-week month, so the grid runs Feb 1 - Mar 14.
    await waitFor(() =>
      expect(api.calendar.listByRange).toHaveBeenCalledWith(
        '2026-02-01',
        '2026-03-14'
      )
    );
  });
});

// Open the details modal on the Feb 4 occurrence of a Mon-Fri series.
async function openRecurringEvent() {
  vi.mocked(api.calendar.listByRange).mockResolvedValue([
    event({
      date: '2026-02-04',
      occurrenceDate: '2026-02-04',
      isRecurring: true,
    }),
  ]);
  vi.mocked(api.calendar.get).mockResolvedValue(
    event({
      repeatFreq: 'weekly',
      repeatInterval: 1,
      repeatByweekday: '1,2,3,4,5',
    })
  );
  renderCalendar();
  fireEvent.click(await screen.findByText(/Work/));
  return screen.findByText('weekdays', { exact: false });
}

describe('recurring occurrences', () => {
  it('renders one badged chip per occurrence of a series', async () => {
    vi.mocked(api.calendar.listByRange).mockResolvedValue([
      event({
        date: '2026-02-02',
        occurrenceDate: '2026-02-02',
        isRecurring: true,
      }),
      event({
        date: '2026-02-03',
        occurrenceDate: '2026-02-03',
        isRecurring: true,
      }),
    ]);
    renderCalendar();
    // Same series id on both — they must not collide as React keys.
    const chips = await screen.findAllByText(/Work/);
    expect(chips.length).toBeGreaterThanOrEqual(2);
    expect(chips[0].textContent).toContain('↻');
  });

  it('opens the details modal from a month cell', async () => {
    // The old month view had no per-event click target, so the modal was
    // unreachable from it.
    vi.mocked(api.calendar.listByRange).mockResolvedValue([
      event({
        date: '2026-02-04',
        occurrenceDate: '2026-02-04',
        isRecurring: true,
      }),
    ]);
    vi.mocked(api.calendar.get).mockResolvedValue(
      event({
        repeatFreq: 'weekly',
        repeatInterval: 1,
        repeatByweekday: '1,2,3,4,5',
      })
    );
    renderCalendar();
    fireEvent.click(await screen.findByText(/Work/));
    expect(await screen.findByText('weekdays', { exact: false })).toBeTruthy();
  });

  it('offers occurrence-vs-series delete for a recurring event', async () => {
    vi.mocked(api.calendar.listByRange).mockResolvedValue([
      event({
        date: '2026-02-04',
        occurrenceDate: '2026-02-04',
        isRecurring: true,
      }),
    ]);
    vi.mocked(api.calendar.get).mockResolvedValue(
      event({
        repeatFreq: 'weekly',
        repeatInterval: 1,
        repeatByweekday: '1,2,3,4,5',
      })
    );
    renderCalendar();
    fireEvent.click(await screen.findByText(/Work/));
    fireEvent.click(await screen.findByText('Delete'));
    fireEvent.click(await screen.findByText('This occurrence'));
    await waitFor(() =>
      expect(api.calendar.skipOccurrence).toHaveBeenCalledWith(
        'e1',
        '2026-02-04'
      )
    );
    expect(api.calendar.delete).not.toHaveBeenCalled();
  });

  it('ends the series from this date instead of erasing history', async () => {
    // Cancelling an ongoing commitment must leave the days already lived alone.
    await openRecurringEvent();
    fireEvent.click(await screen.findByText('Delete'));
    fireEvent.click(await screen.findByText('This and future'));
    await waitFor(() =>
      expect(api.calendar.endSeries).toHaveBeenCalledWith('e1', '2026-02-04')
    );
    expect(api.calendar.delete).not.toHaveBeenCalled();
  });

  it('still offers an outright delete for a series created by mistake', async () => {
    await openRecurringEvent();
    fireEvent.click(await screen.findByText('Delete'));
    fireEvent.click(await screen.findByText('Whole series'));
    await waitFor(() => expect(api.calendar.delete).toHaveBeenCalledWith('e1'));
    expect(api.calendar.endSeries).not.toHaveBeenCalled();
  });

  it('deletes a one-off outright, with no occurrence prompt', async () => {
    vi.mocked(api.calendar.listByRange).mockResolvedValue([
      event({ title: 'Dentist', date: '2026-02-04' }),
    ]);
    vi.mocked(api.calendar.get).mockResolvedValue(event({ title: 'Dentist' }));
    renderCalendar();
    fireEvent.click(await screen.findByText(/Dentist/));
    fireEvent.click(await screen.findByText('Delete Event'));
    await waitFor(() => expect(api.calendar.delete).toHaveBeenCalledWith('e1'));
  });
});

describe('editing a recurring event', () => {
  it('splits the series so past occurrences keep their old values', async () => {
    await openRecurringEvent();
    fireEvent.click(screen.getByText('Edit'));
    fireEvent.change(screen.getByLabelText('Start time'), {
      target: { value: '10:00' },
    });
    fireEvent.change(screen.getByLabelText('End time'), {
      target: { value: '18:00' },
    });
    fireEvent.click(screen.getByText('Save'));
    // A recurring edit must ask before it can rewrite anything.
    fireEvent.click(await screen.findByText('This and future'));

    await waitFor(() =>
      expect(api.calendar.updateFrom).toHaveBeenCalledWith(
        'e1',
        '2026-02-04',
        expect.objectContaining({ time: '10:00', endTime: '18:00' })
      )
    );
    expect(api.calendar.update).not.toHaveBeenCalled();
  });

  it('rewrites every occurrence only when explicitly asked', async () => {
    await openRecurringEvent();
    fireEvent.click(screen.getByText('Edit'));
    fireEvent.change(screen.getByLabelText('Title'), {
      target: { value: 'Wrok' },
    });
    fireEvent.click(screen.getByText('Save'));
    fireEvent.click(await screen.findByText('All events'));

    await waitFor(() =>
      expect(api.calendar.update).toHaveBeenCalledWith(
        'e1',
        expect.objectContaining({ title: 'Wrok' })
      )
    );
    expect(api.calendar.updateFrom).not.toHaveBeenCalled();
  });

  it('saves a one-off directly, with no scope prompt', async () => {
    vi.mocked(api.calendar.listByRange).mockResolvedValue([
      event({ title: 'Dentist', date: '2026-02-04' }),
    ]);
    vi.mocked(api.calendar.get).mockResolvedValue(event({ title: 'Dentist' }));
    renderCalendar();
    fireEvent.click(await screen.findByText(/Dentist/));
    fireEvent.click(await screen.findByText('Edit'));
    fireEvent.change(screen.getByLabelText('Title'), {
      target: { value: 'Dentist checkup' },
    });
    fireEvent.click(screen.getByText('Save'));

    await waitFor(() =>
      expect(api.calendar.update).toHaveBeenCalledWith(
        'e1',
        expect.objectContaining({ title: 'Dentist checkup' })
      )
    );
    expect(screen.queryByText('This and future')).toBeNull();
  });
});

describe('creating a recurring event', () => {
  it('sends the weekday rule for a Mon-Fri series', async () => {
    const { container } = renderCalendar();
    const cells = await waitFor(() => {
      const found = container.querySelectorAll('[data-testid="month-cell"]');
      expect(found).toHaveLength(42);
      return found;
    });
    fireEvent.click(cells[3]); // Wednesday 2026-02-04
    fireEvent.click(screen.getByText('+ Add'));
    fireEvent.change(screen.getByPlaceholderText('Event title'), {
      target: { value: 'Work' },
    });

    const [start, end] = container.querySelectorAll('input[type="time"]');
    fireEvent.change(start, { target: { value: '09:00' } });
    fireEvent.change(end, { target: { value: '17:00' } });

    fireEvent.change(screen.getByRole('combobox'), {
      target: { value: 'weekly' },
    });
    // Weekly seeds the selected day (Wed = 3); add the rest of the workweek.
    for (const label of ['Mon', 'Tue', 'Thu', 'Fri']) {
      fireEvent.click(screen.getByLabelText(label));
    }
    fireEvent.click(screen.getByText('Save'));

    // `api.calendar.create` is the mutationFn itself, so React Query appends
    // its own context argument — assert on the payload, not the whole call.
    await waitFor(() => expect(api.calendar.create).toHaveBeenCalled());
    expect(vi.mocked(api.calendar.create).mock.calls[0][0]).toEqual(
      expect.objectContaining({
        title: 'Work',
        date: '2026-02-04',
        time: '09:00',
        endTime: '17:00',
        repeatFreq: 'weekly',
        repeatInterval: 1,
        repeatByweekday: [1, 2, 3, 4, 5],
      })
    );
  });

  it('sends null recurrence for a plain one-off', async () => {
    const { container } = renderCalendar();
    const cells = await waitFor(() => {
      const found = container.querySelectorAll('[data-testid="month-cell"]');
      expect(found).toHaveLength(42);
      return found;
    });
    fireEvent.click(cells[3]);
    fireEvent.click(screen.getByText('+ Add'));
    fireEvent.change(screen.getByPlaceholderText('Event title'), {
      target: { value: 'Dentist' },
    });
    fireEvent.click(screen.getByText('Save'));

    await waitFor(() => expect(api.calendar.create).toHaveBeenCalled());
    expect(vi.mocked(api.calendar.create).mock.calls[0][0]).toEqual(
      expect.objectContaining({
        title: 'Dentist',
        repeatFreq: null,
        repeatInterval: null,
        repeatByweekday: null,
      })
    );
  });
});
