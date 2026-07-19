// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { SttPanel } from './SttPanel';

vi.mock('../../hooks/useRecorder', () => ({
  useRecorder: (onTranscript: (text: string) => void) => ({
    status: 'idle',
    error: '',
    start: vi.fn(async () => {
      onTranscript('hello from the journal button');
    }),
    stop: vi.fn(),
  }),
}));

vi.mock('../../hooks/api', () => ({
  api: {
    stt: {
      listenerState: vi.fn().mockResolvedValue({
        recording: false,
        transcribing: false,
        mode: null,
      }),
    },
    meetings: {
      active: vi.fn().mockResolvedValue(null),
      start: vi.fn(),
      stop: vi.fn(),
    },
    journal: {
      createFromVoice: vi.fn().mockResolvedValue({ id: 'j1' }),
    },
  },
}));

function renderWithProviders(children: ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}

describe('SttPanel', () => {
  beforeEach(() => {
    vi.mocked(api.journal.createFromVoice).mockClear();
  });

  it('routes the Journal button transcript to the journal API, not the editor callback', async () => {
    const onTranscribed = vi.fn();
    renderWithProviders(
      <SttPanel onTranscribed={onTranscribed} onMeetingUploaded={() => {}} />
    );

    fireEvent.click(await screen.findByText('Journal'));

    await waitFor(() =>
      expect(api.journal.createFromVoice).toHaveBeenCalledWith(
        'hello from the journal button'
      )
    );
    expect(onTranscribed).not.toHaveBeenCalled();
  });

  it('the Record button still routes its transcript to the editor callback', async () => {
    const onTranscribed = vi.fn();
    renderWithProviders(
      <SttPanel onTranscribed={onTranscribed} onMeetingUploaded={() => {}} />
    );

    fireEvent.click(await screen.findByText('Record'));

    await waitFor(() =>
      expect(onTranscribed).toHaveBeenCalledWith(
        'hello from the journal button'
      )
    );
    expect(api.journal.createFromVoice).not.toHaveBeenCalled();
  });
});
