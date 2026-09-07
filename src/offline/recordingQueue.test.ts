import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient } from '@tanstack/react-query';

vi.hoisted(() => {
  (globalThis as { indexedDB?: unknown }).indexedDB = {};
});

const idb = new Map<unknown, unknown>();
vi.mock('idb-keyval', () => ({
  createStore: () => ({}),
  get: async (k: unknown) => idb.get(k),
  set: async (k: unknown, v: unknown) => void idb.set(k, v),
  del: async (k: unknown) => void idb.delete(k),
  keys: async () => [...idb.keys()],
}));

const createRecording = vi.fn();
const createFoodRecording = vi.fn();
vi.mock('../hooks/api', async () => {
  const actual =
    await vi.importActual<typeof import('../hooks/api')>('../hooks/api');
  return {
    ...actual,
    api: {
      journal: { createRecording: (...a: unknown[]) => createRecording(...a) },
      food: { createRecording: (...a: unknown[]) => createFoodRecording(...a) },
    },
  };
});

const { ApiError } = await import('../hooks/api');
const {
  appendChunk,
  beginRecording,
  finalizeRecording,
  getRecording,
  listRecordings,
} = await import('./recordingStore');
const { MUTATION_KEYS, registerOfflineMutationDefaults } =
  await import('./mutationDefaults');
const {
  enqueueFoodRecording,
  enqueueRecordingUpload,
  handleFinishedRecording,
  resumeStoredRecordings,
} = await import('./recordingQueue');

type RetryFn = (failureCount: number, error: Error) => boolean;

function retryPolicy(qc: QueryClient): RetryFn {
  return qc.getMutationDefaults(MUTATION_KEYS.journalRecording)
    .retry as RetryFn;
}

/**
 * A client with the real registered defaults, except that retries are disabled
 * so a test can observe the first failure without waiting out the production
 * backoff. The policy itself is asserted directly, further down.
 */
function client() {
  const qc = new QueryClient({
    defaultOptions: { mutations: { networkMode: 'always', retry: false } },
  });
  registerOfflineMutationDefaults(qc);
  // Both recording mutations carry their own retry policy; without clearing
  // each one a failing test waits out the production backoff instead of
  // observing the first failure.
  for (const key of [
    MUTATION_KEYS.journalRecording,
    MUTATION_KEYS.foodRecording,
  ]) {
    qc.setMutationDefaults(key, {
      ...qc.getMutationDefaults(key),
      retry: false,
    });
  }
  return qc;
}

async function storedRecording(
  mode: 'audio' | 'transcribe' = 'audio',
  opts: {
    entryId?: string;
    idea?: { id: string; repoId?: string };
    fic?: { ficId: string; chapterId?: string };
    food?: { id: string };
  } = {}
) {
  const rec = await beginRecording(mode, 'audio/mp4', opts);
  await appendChunk(rec.id, new Blob(['spoken words']));
  return (await finalizeRecording(rec.id))!;
}

beforeEach(() => {
  idb.clear();
  createRecording.mockReset();
  createFoodRecording.mockReset();
});

describe('uploading a stored recording', () => {
  it('sends the audio under the recording id and then lets it go', async () => {
    const rec = await storedRecording();
    createRecording.mockResolvedValue({ id: rec.id, attachment: {} });

    await enqueueRecordingUpload(client(), rec.id, 'Recording');

    const [blob, opts] = createRecording.mock.calls[0];
    expect(await (blob as Blob).text()).toBe('spoken words');
    // One id for both, which is what makes a replay a no-op server-side.
    expect(opts).toMatchObject({ id: rec.id, attachmentId: rec.id });
    // Confirmed stored — the only circumstance in which the audio is deleted.
    expect(await listRecordings()).toEqual([]);
  });

  it('keeps the audio when the upload fails', async () => {
    const rec = await storedRecording();
    createRecording.mockRejectedValue(new Error('Failed to fetch'));

    await expect(enqueueRecordingUpload(client(), rec.id)).rejects.toThrow();

    const after = (await getRecording(rec.id))!;
    expect(after.attempts).toBe(1);
    expect(after.lastError).toBe('Failed to fetch');
    expect(after.failed).toBe(false);
  });

  it('retries a server or network problem but not a refusal', () => {
    const qc = new QueryClient();
    registerOfflineMutationDefaults(qc);
    const retry = retryPolicy(qc);

    expect(retry(0, new Error('Failed to fetch'))).toBe(true);
    expect(retry(0, new ApiError('boom', 500))).toBe(true);
    // Retrying a 4xx changes nothing; the audio is kept and offered instead.
    expect(retry(0, new ApiError('file is too large', 413))).toBe(false);
    // And it gives up eventually rather than looping forever.
    expect(retry(5, new ApiError('boom', 500))).toBe(false);
  });

  it('stops retrying a recording the server has refused, but keeps it', async () => {
    const rec = await storedRecording();
    createRecording.mockRejectedValue(new ApiError('file is too large', 413));

    await expect(enqueueRecordingUpload(client(), rec.id)).rejects.toThrow();

    const after = (await getRecording(rec.id))!;
    expect(after.failed).toBe(true);
    // Refused is not the same as worthless: it is still offered for download.
    expect(await listRecordings()).toHaveLength(1);
  });
});

describe('what happens to a finished recording', () => {
  it('queues an audio recording as its own entry', async () => {
    const rec = await storedRecording('audio');
    createRecording.mockResolvedValue({ id: rec.id, attachment: {} });

    await handleFinishedRecording(client(), rec);

    expect(createRecording).toHaveBeenCalledTimes(1);
    expect(createRecording.mock.calls[0][1]).toMatchObject({
      transcribe: false,
    });
  });

  it('uploads a journal recording and asks the server to transcribe it', async () => {
    const rec = await storedRecording('transcribe');
    createRecording.mockResolvedValue({ id: rec.id, attachment: {} });

    await handleFinishedRecording(client(), rec);

    expect(createRecording).toHaveBeenCalledTimes(1);
    expect(createRecording.mock.calls[0][1]).toMatchObject({
      transcribe: true,
    });
    expect(await listRecordings()).toEqual([]);
  });
});

describe('sending a staged clip', () => {
  // What `useClipStage.commit` calls for every clip a composer staged. The
  // entry, idea, fic or meal it belongs to was decided when recording started
  // and is stored beside the audio; this is where it is handed over.
  it('uploads against the entry id, not the recording id', async () => {
    const rec = await storedRecording('transcribe');
    createRecording.mockResolvedValue({ id: 'entry-7', attachment: {} });

    await enqueueRecordingUpload(client(), rec.id, 'Recording', {
      entryId: 'entry-7',
    });

    expect(createRecording).toHaveBeenCalledTimes(1);
    const opts = createRecording.mock.calls[0][1];
    expect(opts.id).toBe('entry-7');
    // The attachment keeps the recording's own id — that is what makes a replay
    // of this upload a no-op instead of a second copy of the file.
    expect(opts.attachmentId).toBe(rec.id);
    // Several clips can share one entry id, so the server is what appends their
    // transcripts in order.
    expect(opts.transcribe).toBe(true);
  });

  it('lets go of the audio only once the server has it', async () => {
    const rec = await storedRecording('transcribe');
    createRecording.mockRejectedValue(new Error('offline'));

    await expect(
      enqueueRecordingUpload(client(), rec.id, 'Recording', {
        entryId: 'entry-7',
      })
    ).rejects.toThrow();

    expect(await listRecordings()).toHaveLength(1);
  });

  it('carries the idea it is also being captured as', async () => {
    const rec = await storedRecording('transcribe', {
      idea: { id: 'idea-1', repoId: 'repo-1' },
    });
    createRecording.mockResolvedValue({ id: 'entry-7', attachment: {} });

    await enqueueRecordingUpload(client(), rec.id, 'Idea', {
      entryId: 'entry-7',
      idea: rec.idea,
    });

    expect(createRecording.mock.calls[0][1]).toMatchObject({
      ideaId: 'idea-1',
      repoId: 'repo-1',
    });
  });

  it('carries the fic and chapter it is commentary on', async () => {
    const rec = await storedRecording('transcribe', {
      fic: { ficId: 'fic-1', chapterId: 'ch-1' },
    });
    createRecording.mockResolvedValue({ id: 'entry-7', attachment: {} });

    await enqueueRecordingUpload(client(), rec.id, 'Commentary', {
      entryId: 'entry-7',
      fic: rec.fic,
    });

    expect(createRecording.mock.calls[0][1]).toMatchObject({
      ficId: 'fic-1',
      chapterId: 'ch-1',
    });
  });

  it('sends a meal clip to the food route instead, with its position', async () => {
    // A meal is not a journal entry — it is a food_entries row the feed borrows
    // — so its audio is food media rather than a journal attachment.
    const rec = await storedRecording('transcribe', { food: { id: 'meal-1' } });
    createFoodRecording.mockResolvedValue({ id: 'meal-1', media: {} });

    await enqueueFoodRecording(client(), rec.id, 'meal-1', 1);

    expect(createRecording).not.toHaveBeenCalled();
    expect(createFoodRecording.mock.calls[0][1]).toMatchObject({
      id: 'meal-1',
      mediaId: rec.id,
      position: 1,
    });
    // Confirmed stored, so the audio may go.
    expect(await listRecordings()).toHaveLength(0);
  });

  it('keeps a meal clip when the upload fails', async () => {
    const rec = await storedRecording('transcribe', { food: { id: 'meal-1' } });
    createFoodRecording.mockRejectedValue(new Error('offline'));

    await expect(
      enqueueFoodRecording(client(), rec.id, 'meal-1', 0)
    ).rejects.toThrow();

    expect(await listRecordings()).toHaveLength(1);
  });
});

describe('picking up recordings from a previous session', () => {
  it('uploads a recording the app died in the middle of', async () => {
    // Never finalized: the tab was discarded while it was still recording.
    const rec = await beginRecording('audio', 'audio/mp4');
    await appendChunk(rec.id, new Blob(['half a thought']));
    createRecording.mockResolvedValue({ id: rec.id, attachment: {} });

    await resumeStoredRecordings(client());
    await vi.waitFor(() => expect(createRecording).toHaveBeenCalledTimes(1));

    const [blob] = createRecording.mock.calls[0];
    expect(await (blob as Blob).text()).toBe('half a thought');
    expect(await listRecordings()).toEqual([]);
  });

  it('leaves a refused recording alone rather than hammering the server', async () => {
    const rec = await storedRecording();
    createRecording.mockRejectedValue(new ApiError('unsupported', 400));
    await enqueueRecordingUpload(client(), rec.id).catch(() => undefined);
    createRecording.mockClear();

    await resumeStoredRecordings(client());

    expect(createRecording).not.toHaveBeenCalled();
    expect(await listRecordings()).toHaveLength(1);
  });

  it('puts a rescued clip back on its entry rather than making a new one', async () => {
    // A composer stages clips and sends them together, so the entry id is
    // minted at the first chunk and written beside the audio. A rescue that has
    // forgotten it files the clip as a journal entry of its own, next to the
    // entry it was recorded alongside — the audio survives, detached from the
    // words. This is the reason the id is minted then rather than at Save.
    const rec = await storedRecording('transcribe', { entryId: 'entry-7' });
    createRecording.mockResolvedValue({ id: 'entry-7', attachment: {} });

    await resumeStoredRecordings(client());
    await vi.waitFor(() => expect(createRecording).toHaveBeenCalledTimes(1));

    const opts = createRecording.mock.calls[0][1];
    expect(opts.id).toBe('entry-7');
    expect(opts.attachmentId).toBe(rec.id);
  });

  it('sends a rescued meal clip to the food route, not the journal', async () => {
    // Filing it as a journal entry would put the words in a different tab from
    // the photograph of the plate they were spoken over.
    const rec = await storedRecording('transcribe', { food: { id: 'meal-1' } });
    createFoodRecording.mockResolvedValue({ id: 'meal-1', media: {} });

    await resumeStoredRecordings(client());
    await vi.waitFor(() =>
      expect(createFoodRecording).toHaveBeenCalledTimes(1)
    );

    expect(createRecording).not.toHaveBeenCalled();
    expect(createFoodRecording.mock.calls[0][1]).toMatchObject({
      id: 'meal-1',
      mediaId: rec.id,
    });
  });
});
