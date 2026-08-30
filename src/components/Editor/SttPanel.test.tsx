// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { SttPanel } from './SttPanel';

const durableStarts: Array<{ mode?: string; durable?: boolean }> = [];

vi.mock('../../hooks/useRecorder', () => ({
  useRecorder: (
    onTranscript: (text: string) => void,
    _onAudio: unknown,
    options?: {
      onRecording?: (rec: { id: string; mode: string }) => void | Promise<void>;
    }
  ) => ({
    status: 'idle',
    canTranscribe: true,
    error: '',
    // start('audio') is the no-transcription path: the stored recording goes
    // straight to onRecording and nothing is ever handed to onTranscript.
    start: vi.fn(async (mode?: string, opts?: { durable?: boolean }) => {
      durableStarts.push({ mode, durable: opts?.durable });
      if (mode === 'audio' || mode === 'transcribe')
        await options?.onRecording?.({ id: 'rec-1', mode });
      else onTranscript('hello from the journal button');
    }),
    stop: vi.fn(),
  }),
}));

// The panel's job is to hand a finished recording to the queue; what the queue
// then does with it (upload, retry, keep) is covered by recordingQueue.test.ts.
const handleFinishedRecording = vi.fn().mockResolvedValue(undefined);
vi.mock('../../offline/recordingQueue', () => ({
  handleFinishedRecording: (...args: unknown[]) =>
    handleFinishedRecording(...args),
}));

vi.mock('./PendingRecordings', () => ({
  PendingRecordings: () => null,
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
      createRecording: vi.fn().mockResolvedValue({ id: 'j2', attachment: {} }),
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
    vi.mocked(api.journal.createRecording).mockClear();
    handleFinishedRecording.mockClear();
    durableStarts.length = 0;
  });

  it('hands the Journal recording to the durable upload queue', async () => {
    const onTranscribed = vi.fn();
    renderWithProviders(
      <SttPanel onTranscribed={onTranscribed} onMeetingUploaded={() => {}} />
    );

    fireEvent.click(await screen.findByText('Journal'));

    await waitFor(() =>
      expect(handleFinishedRecording).toHaveBeenCalledTimes(1)
    );
    expect(handleFinishedRecording.mock.calls[0][1]).toMatchObject({
      id: 'rec-1',
      mode: 'transcribe',
    });
    expect(api.journal.createFromVoice).not.toHaveBeenCalled();
    expect(onTranscribed).not.toHaveBeenCalled();
  });

  it('the Transcribe button routes its transcript to the editor callback', async () => {
    const onTranscribed = vi.fn();
    renderWithProviders(
      <SttPanel onTranscribed={onTranscribed} onMeetingUploaded={() => {}} />
    );

    fireEvent.click(await screen.findByText('Transcribe'));

    await waitFor(() =>
      expect(onTranscribed).toHaveBeenCalledWith(
        'hello from the journal button'
      )
    );
    expect(api.journal.createFromVoice).not.toHaveBeenCalled();
  });

  it('the Record button hands the stored recording to the queue, untranscribed', async () => {
    const onTranscribed = vi.fn();
    renderWithProviders(
      <SttPanel onTranscribed={onTranscribed} onMeetingUploaded={() => {}} />
    );

    fireEvent.click(await screen.findByText('Record'));

    await waitFor(() =>
      expect(handleFinishedRecording).toHaveBeenCalledTimes(1)
    );
    expect(handleFinishedRecording.mock.calls[0][1]).toMatchObject({
      id: 'rec-1',
    });
    // The whole point: no speech-to-text, and nothing typed into the editor.
    expect(api.journal.createFromVoice).not.toHaveBeenCalled();
    expect(onTranscribed).not.toHaveBeenCalled();
  });

  it('records the journal buttons durably and the plain one not', async () => {
    renderWithProviders(
      <SttPanel onTranscribed={vi.fn()} onMeetingUploaded={() => {}} />
    );

    fireEvent.click(await screen.findByText('Record'));
    fireEvent.click(await screen.findByText('Journal'));
    fireEvent.click(await screen.findByText('Transcribe'));

    // The two journal buttons persist their audio; the third dictates into
    // whatever text field is open, where a lost take is retyped, not lost.
    await waitFor(() => expect(durableStarts).toHaveLength(3));
    expect(durableStarts).toEqual([
      { mode: 'audio', durable: true },
      { mode: 'transcribe', durable: true },
      { mode: undefined, durable: undefined },
    ]);
  });
});
