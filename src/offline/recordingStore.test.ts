import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.hoisted(() => {
  // recordingStore picks between IndexedDB and its in-memory fallback once, at
  // module load. Node has no indexedDB, so without this the tests would prove
  // nothing about the path that has to survive a reload.
  (globalThis as { indexedDB?: unknown }).indexedDB = {};
});

// In-memory stand-in for IndexedDB, following the pattern in
// persist.integration.test.ts — the real store code paths run, no browser.
const idb = new Map<unknown, unknown>();
vi.mock('idb-keyval', () => ({
  createStore: () => ({}),
  get: async (k: unknown) => idb.get(k),
  set: async (k: unknown, v: unknown) => void idb.set(k, v),
  del: async (k: unknown) => void idb.delete(k),
  keys: async () => [...idb.keys()],
}));

const {
  appendChunk,
  assembleBlob,
  beginRecording,
  deleteRecording,
  finalizeRecording,
  getRecording,
  listRecordings,
  markAttempt,
} = await import('./recordingStore');

beforeEach(() => idb.clear());

describe('recordingStore', () => {
  it('stores each chunk under its own key', async () => {
    const rec = await beginRecording('audio', 'audio/mp4');
    await appendChunk(rec.id, new Blob(['a']));
    await appendChunk(rec.id, new Blob(['b']));

    // One key per chunk, not one growing array: appending to a stored array
    // rewrites the whole thing every five seconds, which is quadratic in a long
    // recording.
    const chunkKeys = [...idb.keys()].filter(
      k => typeof k === 'string' && k.startsWith('chunk:')
    );
    expect(chunkKeys).toHaveLength(2);
    expect((await getRecording(rec.id))!.chunkCount).toBe(2);
  });

  it('reassembles the chunks in the order they were recorded', async () => {
    const rec = await beginRecording('audio', 'audio/mp4');
    for (const part of ['one ', 'two ', 'three']) {
      await appendChunk(rec.id, new Blob([part]));
    }

    const blob = await assembleBlob(rec.id);
    expect(await blob!.text()).toBe('one two three');
    expect(blob!.type).toBe('audio/mp4');
  });

  it('survives a reload with the recording still unfinalized', async () => {
    const rec = await beginRecording('transcribe', 'audio/webm');
    await appendChunk(rec.id, new Blob(['mid-sentence']));

    // A reload is exactly this: the module state is gone, the store is not.
    vi.resetModules();
    const fresh = await import('./recordingStore');

    const [restored] = await fresh.listRecordings();
    expect(restored.id).toBe(rec.id);
    expect(restored.finalized).toBe(false);
    expect(await (await fresh.assembleBlob(rec.id))!.text()).toBe(
      'mid-sentence'
    );
  });

  it('uploads the prefix when a chunk is missing rather than nothing', async () => {
    const rec = await beginRecording('audio', 'audio/webm');
    await appendChunk(rec.id, new Blob(['kept']));
    await appendChunk(rec.id, new Blob([' lost']));
    // A torn write: the metadata counts two chunks, only one is there.
    for (const k of [...idb.keys()]) {
      if (typeof k === 'string' && k.endsWith(':000001')) idb.delete(k);
    }

    // A truncated recording still carries most of what was said.
    expect(await (await assembleBlob(rec.id))!.text()).toBe('kept');
  });

  it('records a failed attempt without touching the audio', async () => {
    const rec = await beginRecording('audio', 'audio/webm');
    await appendChunk(rec.id, new Blob(['still here']));
    await finalizeRecording(rec.id);
    await markAttempt(rec.id, 'HTTP 500');

    const after = (await getRecording(rec.id))!;
    expect(after).toMatchObject({
      attempts: 1,
      lastError: 'HTTP 500',
      failed: false,
      finalized: true,
    });
    expect(await (await assembleBlob(rec.id))!.text()).toBe('still here');
  });

  it('marks a terminal failure without discarding the recording', async () => {
    const rec = await beginRecording('audio', 'audio/webm');
    await appendChunk(rec.id, new Blob(['refused']));
    await markAttempt(rec.id, 'file is too large', true);

    // "The server refused it" is not a reason to destroy the audio.
    expect((await getRecording(rec.id))!.failed).toBe(true);
    expect(await listRecordings()).toHaveLength(1);
  });

  it('deletes the metadata and every chunk together', async () => {
    const rec = await beginRecording('audio', 'audio/webm');
    await appendChunk(rec.id, new Blob(['a']));
    await appendChunk(rec.id, new Blob(['b']));

    await deleteRecording(rec.id);

    expect(await listRecordings()).toEqual([]);
    // No orphaned chunks left behind to fill the quota.
    expect([...idb.keys()]).toEqual([]);
  });

  it('finalizing marks how the recording ended', async () => {
    const normal = await beginRecording('audio', 'audio/webm');
    const cut = await beginRecording('audio', 'audio/webm');

    expect((await finalizeRecording(normal.id))!.recovered).toBe(false);
    expect(
      (await finalizeRecording(cut.id, { recovered: true }))!.recovered
    ).toBe(true);
    expect((await getRecording(cut.id))!.endedAt).toBeTypeOf('number');
  });

  it('lists recordings oldest first', async () => {
    const first = await beginRecording('audio', 'audio/webm');
    const second = await beginRecording('transcribe', 'audio/webm');
    expect((await listRecordings()).map(r => r.id)).toEqual([
      first.id,
      second.id,
    ]);
  });
});
