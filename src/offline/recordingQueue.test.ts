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
  attachRecordingToEntry,
  captureFicCommentary,
  captureIdeaRecording,
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

async function storedRecording(
  mode: 'audio' | 'transcribe' = 'audio',
  opts: {
    idea?: { id: string; repoId?: string };
    fic?: { ficId: string; chapterId?: string };
  } = {}
) {
  const rec = await beginRecording(mode, 'audio/mp4', opts);
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

describe('attaching a recording to an entry that already exists', () => {
  // The Journal's Transcribe button records while an entry is open, so the clip
  // belongs on that entry rather than on a new one of its own. It goes through
  // the same durable queue: `POST /api/journal/recordings` does INSERT OR IGNORE
  // on the entry, so an id that already exists means "attach to this".
  it('uploads against the entry id, not the recording id', async () => {
    const rec = await storedRecording('audio');
    createRecording.mockResolvedValue({ id: 'entry-7', attachment: {} });

    await attachRecordingToEntry(client(), rec, 'entry-7');

    expect(createRecording).toHaveBeenCalledTimes(1);
    const opts = createRecording.mock.calls[0][1];
    expect(opts.id).toBe('entry-7');
    // The attachment keeps the recording's own id — that is what makes a replay
    // of this upload a no-op instead of a second copy of the file.
    expect(opts.attachmentId).toBe(rec.id);
  });

  it('does not ask the server to transcribe it a second time', async () => {
    // The text was already fetched in the browser and put in the draft; a
    // server-side pass would burn another CPU transcription and write into the
    // entry body underneath an open editor.
    const rec = await storedRecording('audio');
    createRecording.mockResolvedValue({ id: 'entry-7', attachment: {} });

    await attachRecordingToEntry(client(), rec, 'entry-7');

    expect(createRecording.mock.calls[0][1]).toMatchObject({
      transcribe: false,
    });
  });

  it('lets go of the audio only once the server has it', async () => {
    const rec = await storedRecording('audio');
    createRecording.mockRejectedValue(new Error('offline'));

    await expect(
      attachRecordingToEntry(client(), rec, 'entry-7')
    ).rejects.toThrow();

    expect(await listRecordings()).toHaveLength(1);
  });
});

describe('recording an idea', () => {
  // The Ideas tab's Record button: one upload, which the server turns into a
  // journal entry, its attachment, and the idea — all from one transcription.
  it('uploads the clip with the idea it is also being captured as', async () => {
    const rec = await storedRecording('transcribe', {
      idea: { id: 'idea-9', repoId: 'repo-2' },
    });
    createRecording.mockResolvedValue({ id: rec.id, attachment: {} });

    await captureIdeaRecording(client(), rec);

    expect(createRecording).toHaveBeenCalledTimes(1);
    expect(createRecording.mock.calls[0][1]).toMatchObject({
      id: rec.id,
      attachmentId: rec.id,
      ideaId: 'idea-9',
      repoId: 'repo-2',
      // The server transcribes it — into the entry and into the idea.
      transcribe: true,
    });
    expect(await listRecordings()).toEqual([]);
  });

  it('keeps the audio, and the idea link, when the upload fails', async () => {
    const rec = await storedRecording('transcribe', { idea: { id: 'idea-9' } });
    createRecording.mockRejectedValue(new Error('Failed to fetch'));

    await expect(captureIdeaRecording(client(), rec)).rejects.toThrow();

    const after = (await getRecording(rec.id))!;
    expect(after.idea).toEqual({ id: 'idea-9' });
  });

  it('still knows it was an idea after the app was killed mid-recording', async () => {
    // The idea id is written to the store with the first chunk precisely so the
    // startup sweep can find it: a resumed upload that had forgotten it would
    // file the clip as a plain journal entry and lose the idea entirely.
    const rec = await beginRecording('transcribe', 'audio/mp4', {
      idea: { id: 'idea-9' },
    });
    await appendChunk(rec.id, new Blob(['half a thought']));
    createRecording.mockResolvedValue({ id: rec.id, attachment: {} });

    await resumeStoredRecordings(client());
    await vi.waitFor(() => expect(createRecording).toHaveBeenCalledTimes(1));

    expect(createRecording.mock.calls[0][1]).toMatchObject({
      ideaId: 'idea-9',
    });
  });
});

describe('commentary recorded in the fanfic reader', () => {
  // The reader's Commentary microphone: the clip becomes a journal entry that
  // is linked to the chapter it was spoken over, and the transcript is written
  // onto that entry by the server afterwards.
  it('uploads the clip with the fic and chapter it is commentary on', async () => {
    const rec = await storedRecording('transcribe', {
      fic: { ficId: 'fic-1', chapterId: 'ch-4' },
    });
    createRecording.mockResolvedValue({ id: rec.id, attachment: {} });

    await captureFicCommentary(client(), rec);

    expect(createRecording).toHaveBeenCalledTimes(1);
    expect(createRecording.mock.calls[0][1]).toMatchObject({
      id: rec.id,
      attachmentId: rec.id,
      ficId: 'fic-1',
      chapterId: 'ch-4',
      // The link rides along with the upload rather than following in a second
      // request, so a dropped connection cannot land the audio and lose the
      // chapter it was about.
      transcribe: true,
    });
    expect(await listRecordings()).toEqual([]);
  });

  it('sends no chapter for a PDF fic, which has none', async () => {
    const rec = await storedRecording('transcribe', {
      fic: { ficId: 'fic-1' },
    });
    createRecording.mockResolvedValue({ id: rec.id, attachment: {} });

    await captureFicCommentary(client(), rec);

    expect(createRecording.mock.calls[0][1]).toMatchObject({ ficId: 'fic-1' });
    expect(createRecording.mock.calls[0][1].chapterId).toBeUndefined();
  });

  it('still knows which chapter it was after the app was killed mid-recording', async () => {
    // Same reason as the idea case above: a resumed upload that has forgotten
    // its fic files the commentary as a plain journal entry, with nothing left
    // saying which chapter it was a reaction to.
    const rec = await beginRecording('transcribe', 'audio/mp4', {
      fic: { ficId: 'fic-1', chapterId: 'ch-4' },
    });
    await appendChunk(rec.id, new Blob(['half a thought']));
    createRecording.mockResolvedValue({ id: rec.id, attachment: {} });

    await resumeStoredRecordings(client());
    await vi.waitFor(() => expect(createRecording).toHaveBeenCalledTimes(1));

    expect(createRecording.mock.calls[0][1]).toMatchObject({
      ficId: 'fic-1',
      chapterId: 'ch-4',
      name: 'Commentary',
    });
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
