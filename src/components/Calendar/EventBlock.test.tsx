// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { EventBlock } from './EventBlock';
import { api, type CalendarEvent } from '../../hooks/api';
import { VOICE_EDIT_NOTICE_MS } from '@/lib/calendarVoice';

vi.mock('../../hooks/api', () => ({
  api: { calendar: { transcribe: vi.fn() } },
}));

// The recorder is hardware; what this component owns is what happens to the
// transcript once it arrives, so the hook is replaced by a button-press that
// hands one over directly.
let deliverTranscript: (text: string) => void = () => {};
vi.mock('../../hooks/useRecorder', () => ({
  useRecorder: (onTranscript: (text: string) => void) => {
    deliverTranscript = onTranscript;
    return {
      status: 'idle',
      error: '',
      start: vi.fn(),
      stop: vi.fn(),
      canTranscribe: true,
    };
  },
}));

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

function renderBlock(event = calendarEvent()) {
  const onTranscribed = vi.fn();
  const noop = vi.fn();
  const view = render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <EventBlock
        event={event}
        top={0}
        length={120}
        laneOffsetPx={0}
        zIndex={1}
        onBodyPointerDown={noop}
        onHandlePointerDown={noop}
        onPointerMove={noop}
        onPointerUp={noop}
        onPointerCancel={noop}
        onTranscribed={onTranscribed}
      />
    </QueryClientProvider>
  );
  return { ...view, onTranscribed };
}

const transcribeResult = (applied: string[]) =>
  ({ ...calendarEvent(), voiceEdit: { applied, transcript: 'said' } }) as never;

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.calendar.transcribe).mockResolvedValue(transcribeResult([]));
});

describe('speaking at an event', () => {
  it('sends the transcript with no occurrence for a one-off event', async () => {
    renderBlock();
    deliverTranscript('that was the dentist');
    await waitFor(() =>
      expect(api.calendar.transcribe).toHaveBeenCalledWith(
        'ev-1',
        'that was the dentist',
        undefined
      )
    );
  });

  it('scopes the edit to the occurrence on a recurring series', async () => {
    // The same scoping a drag on this block already applies: a spoken time
    // belongs to the day it was spoken about, not to every future Tuesday.
    renderBlock(
      calendarEvent({
        repeatFreq: 'weekly',
        isRecurring: true,
        occurrenceDate: '2026-07-15',
      })
    );
    deliverTranscript('this one moved to eleven');
    await waitFor(() =>
      expect(api.calendar.transcribe).toHaveBeenCalledWith(
        'ev-1',
        'this one moved to eleven',
        '2026-07-15'
      )
    );
  });

  it('says what the sentence changed, since nothing asked to confirm it', async () => {
    vi.mocked(api.calendar.transcribe).mockResolvedValue(
      transcribeResult(['title', 'time', 'endTime'])
    );
    renderBlock();
    deliverTranscript('call it dentist, quarter past two');
    expect(
      (await screen.findByTestId('calendar-event-voice-notice')).textContent
    ).toBe('Updated name, time');
  });

  it('says so when the sentence changed nothing', async () => {
    renderBlock();
    deliverTranscript('move this to next tuesday');
    expect(
      (await screen.findByTestId('calendar-event-voice-notice')).textContent
    ).toBe('Nothing to change');
  });

  it('reports a failure rather than looking like it worked', async () => {
    vi.mocked(api.calendar.transcribe).mockRejectedValue(new Error('offline'));
    renderBlock();
    deliverTranscript('anything');
    expect(
      (await screen.findByTestId('calendar-event-voice-notice')).textContent
    ).toBe('Could not save that');
  });

  it('shows the time again once the notice has been read', async () => {
    vi.useFakeTimers();
    try {
      renderBlock();
      deliverTranscript('anything');
      await vi.waitFor(() => screen.getByTestId('calendar-event-voice-notice'));
      // Inside act: the timeout's setState is a React update, and without
      // it the assertion runs before the re-render.
      await act(() => vi.advanceTimersByTimeAsync(VOICE_EDIT_NOTICE_MS + 100));
      expect(screen.queryByTestId('calendar-event-voice-notice')).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});
