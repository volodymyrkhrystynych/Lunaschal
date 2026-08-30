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
vi.mock('../hooks/api', async () => {
  const actual =
    await vi.importActual<typeof import('../hooks/api')>('../hooks/api');
  return {
    ...actual,
    api: {
      journal: { createRecording: (...a: unknown[]) => createRecording(...a) },
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
  const key = MUTATION_KEYS.journalRecording;
  qc.setMutationDefaults(key, { ...qc.getMutationDefaults(key), retry: false });
  return qc;
}

async function storedRecording(mode: 'audio' | 'transcribe' = 'audio') {
  const rec = await beginRecording(mode, 'audio/mp4');
  await appendChunk(rec.id, new Blob(['spoken words']));
  return (await finalizeRecording(rec.id))!;
}

beforeEach(() => {
  idb.clear();
  createRecording.mockReset();
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
});
