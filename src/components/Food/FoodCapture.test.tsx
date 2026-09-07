// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { FoodCapture } from './FoodCapture';
import { useFoodCreate } from '../../offline/mutationDefaults';
import { enqueueFoodRecording } from '../../offline/recordingQueue';
import { deleteRecording } from '../../offline/recordingStore';
import { storePhoto } from '../../offline/photoStore';

vi.mock('../../hooks/api', () => ({ api: {} }));

// The recorder is exercised by its own test. Here `start` stands in for the
// user pressing Record and then Stop: it hands back a stored clip, which is all
// stopping is allowed to do now.
interface StartOpts {
  durable?: boolean;
  entryId?: string;
  food?: { id: string };
}
let startCalls: StartOpts[] = [];
let nextClip = 0;
vi.mock('../../hooks/useRecorder', () => ({
  useRecorder: (
    _onTranscript: unknown,
    _onAudio: unknown,
    options: { onRecording?: (rec: unknown) => void } = {}
  ) => ({
    status: 'idle',
    canTranscribe: true,
    error: '',
    start: vi.fn(async (_mode: string, opts: StartOpts = {}) => {
      startCalls.push(opts);
      nextClip += 1;
      options.onRecording?.({
        id: `rec-${nextClip}`,
        food: opts.food,
        startedAt: 0,
        endedAt: 42_000,
      });
    }),
    stop: vi.fn(),
  }),
}));

vi.mock('../../offline/recordingQueue', () => ({
  enqueueRecordingUpload: vi.fn().mockResolvedValue(undefined),
  enqueueFoodRecording: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('../../offline/recordingStore', () => ({
  deleteRecording: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('../../offline/photoStore', () => ({
  storePhoto: vi.fn().mockResolvedValue(undefined),
}));

const createMutate = vi.fn();
vi.mock('../../offline/mutationDefaults', () => ({
  useFoodCreate: vi.fn(),
}));

// Device GPS is best-effort and irrelevant here; resolving null keeps `submit`
// from waiting on a geolocation prompt jsdom will never answer.
vi.mock('../../lib/geo', () => ({ currentPosition: async () => null }));

function renderIt() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <FoodCapture />
    </QueryClientProvider>
  );
}

const record = () => fireEvent.click(screen.getByTestId('food-capture-record'));
const logIt = () => fireEvent.click(screen.getByText('Log it'));

describe('FoodCapture voice clips', () => {
  beforeEach(() => {
    startCalls = [];
    nextClip = 0;
    createMutate.mockClear();
    vi.mocked(enqueueFoodRecording).mockClear();
    vi.mocked(deleteRecording).mockClear();
    vi.mocked(storePhoto).mockClear();
    vi.mocked(useFoodCreate).mockReturnValue({
      mutate: createMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useFoodCreate>);
  });

  it('stages the clip on stop and never blocks the save', async () => {
    // The bug this replaces: the mic held the audio in memory, blocked on
    // POST /api/transcribe, and then discarded the recording — and because
    // `canSubmit` never checked the transcribing state, pressing "Log it"
    // mid-transcription saved the meal without the sentence describing it.
    renderIt();
    record();

    expect(await screen.findByText('Clip 1 · 0:42')).toBeTruthy();
    expect(enqueueFoodRecording).not.toHaveBeenCalled();
    expect((screen.getByText('Log it') as HTMLButtonElement).disabled).toBe(
      false
    );
  });

  it('mints the meal id at the first chunk and logs the clip under it', async () => {
    renderIt();
    record();
    await screen.findByText('Clip 1 · 0:42');

    const mealId = startCalls[0].food?.id;
    expect(mealId).toEqual(expect.any(String));
    // Stored beside the audio, so a clip rescued by the boot sweep lands on
    // this meal rather than becoming a stray journal entry.
    expect(startCalls[0].durable).toBe(true);

    logIt();

    await waitFor(() => expect(createMutate).toHaveBeenCalled());
    expect(createMutate.mock.calls[0][0]).toMatchObject({
      id: mealId,
      pendingClips: 1,
    });
    await waitFor(() =>
      expect(enqueueFoodRecording).toHaveBeenCalledWith(
        expect.anything(),
        'rec-1',
        mealId,
        0
      )
    );
  });

  it('takes several clips and sends them in the order they were spoken', async () => {
    renderIt();
    record();
    await screen.findByText('Clip 1 · 0:42');
    record();
    await screen.findByText('Clip 2 · 0:42');

    logIt();

    await waitFor(() => expect(enqueueFoodRecording).toHaveBeenCalledTimes(2));
    // The position rides along, so the transcripts are appended to the meal in
    // the order they were spoken rather than the order they happen to land.
    expect(
      vi.mocked(enqueueFoodRecording).mock.calls.map(c => [c[1], c[3]])
    ).toEqual([
      ['rec-1', 0],
      ['rec-2', 1],
    ]);
    expect(createMutate.mock.calls[0][0].pendingClips).toBe(2);
  });

  it('logs a meal that was only spoken', async () => {
    renderIt();
    expect((screen.getByText('Log it') as HTMLButtonElement).disabled).toBe(
      true
    );

    record();
    await screen.findByText('Clip 1 · 0:42');
    logIt();

    // No text, no photo — `pendingClips` is what tells the server this is a
    // meal rather than an empty request, so its GPS and capture time survive.
    await waitFor(() => expect(createMutate).toHaveBeenCalled());
    expect(createMutate.mock.calls[0][0].text).toBeUndefined();
    expect(createMutate.mock.calls[0][0].pendingClips).toBe(1);
  });

  it('discards the audio when a staged clip is removed by hand', async () => {
    renderIt();
    record();
    await screen.findByText('Clip 1 · 0:42');

    fireEvent.click(screen.getByLabelText('Discard Clip 1 · 0:42'));

    expect(screen.queryByText('Clip 1 · 0:42')).toBeNull();
    await waitFor(() => expect(deleteRecording).toHaveBeenCalledWith('rec-1'));
  });

  it('clears the staged clips once the meal is logged', async () => {
    renderIt();
    record();
    await screen.findByText('Clip 1 · 0:42');

    logIt();

    await waitFor(() => expect(screen.queryByText('Clip 1 · 0:42')).toBeNull());
    // A second meal gets its own id rather than piling onto the first.
    record();
    await screen.findByText('Clip 1 · 0:42');
    expect(startCalls[1].food?.id).not.toBe(startCalls[0].food?.id);
  });
});
