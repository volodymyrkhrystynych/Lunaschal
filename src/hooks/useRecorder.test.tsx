// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useRecorder } from './useRecorder';
import { installFakeMediaRecorder } from '../test/mediaRecorder';

// jsdom has no indexedDB, and recordingStore decides between the real store and
// its in-memory fallback once, at module load — which happens while the static
// imports above are still being evaluated. Hence vi.hoisted: without it the
// tests would quietly exercise the fallback and prove nothing about the path
// that actually has to survive a reload.
vi.hoisted(() => {
  (globalThis as { indexedDB?: unknown }).indexedDB = {};
});

// The store is the whole point of durable mode, so it is exercised for real
// against an in-memory stand-in for IndexedDB rather than mocked out.
const idb = new Map<unknown, unknown>();
vi.mock('idb-keyval', () => ({
  createStore: () => ({}),
  get: async (k: unknown) => idb.get(k),
  set: async (k: unknown, v: unknown) => void idb.set(k, v),
  del: async (k: unknown) => void idb.delete(k),
  keys: async () => [...idb.keys()],
}));

const { listRecordings, assembleBlob } =
  await import('../offline/recordingStore');

describe('useRecorder', () => {
  const originalMediaDevices = navigator.mediaDevices;

  // Flush first, then clear: unmounting a still-recording hook finalizes it,
  // and that write lands after the previous test's afterEach has run.
  beforeEach(async () => {
    await new Promise(resolve => setTimeout(resolve, 0));
    idb.clear();
  });

  afterEach(() => {
    Object.defineProperty(navigator, 'mediaDevices', {
      value: originalMediaDevices,
      configurable: true,
    });
    vi.unstubAllGlobals();
  });

  it('surfaces a clear HTTPS-required error when mediaDevices is unavailable', async () => {
    // Mirrors iOS Safari on an insecure origin: navigator.mediaDevices itself
    // is undefined, so calling .getUserMedia would throw a raw TypeError.
    Object.defineProperty(navigator, 'mediaDevices', {
      value: undefined,
      configurable: true,
    });

    const onTranscript = vi.fn();
    const { result } = renderHook(() => useRecorder(onTranscript));

    await act(async () => {
      await result.current.start();
    });

    expect(result.current.status).toBe('idle');
    expect(result.current.error).toMatch(/https/i);
    expect(onTranscript).not.toHaveBeenCalled();
  });

  it("start('audio') hands the blob to onAudio and never calls /api/transcribe", async () => {
    const fake = installFakeMediaRecorder();
    const fetchSpy = vi.spyOn(globalThis, 'fetch');

    const onTranscript = vi.fn();
    const onAudio = vi.fn();
    const { result } = renderHook(() => useRecorder(onTranscript, onAudio));

    await act(async () => {
      await result.current.start('audio');
    });
    expect(result.current.status).toBe('recording');

    await act(async () => {
      fake.emit();
      result.current.stop();
      await fake.stop();
    });

    expect(onAudio).toHaveBeenCalledTimes(1);
    expect(onAudio.mock.calls[0][0]).toBeInstanceOf(Blob);
    expect(onTranscript).not.toHaveBeenCalled();
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(result.current.status).toBe('idle');
  });

  it('a failed save surfaces on the recorder rather than disappearing', async () => {
    const fake = installFakeMediaRecorder();
    const { result } = renderHook(() =>
      useRecorder(vi.fn(), () => {
        throw new Error('Disk full');
      })
    );

    await act(async () => {
      await result.current.start('audio');
    });
    await act(async () => {
      result.current.stop();
      await fake.stop();
    });

    expect(result.current.error).toBe('Disk full');
    expect(result.current.status).toBe('idle');
  });

  // --- durable mode ---------------------------------------------------------
  //
  // The bug being fixed: a journal recording existed only inside MediaRecorder
  // and then briefly as a Blob in a closure, so a screen lock, a backgrounded
  // tab or a failed upload destroyed it outright.

  it('writes each chunk to the store while still recording', async () => {
    const fake = installFakeMediaRecorder();
    const { result } = renderHook(() => useRecorder(vi.fn(), undefined, {}));

    await act(async () => {
      await result.current.start('audio', { durable: true });
    });

    await act(async () => {
      fake.emit(new Blob(['one']));
      fake.emit(new Blob(['two']));
    });

    // Still recording, and the audio is already durable.
    expect(result.current.status).toBe('recording');
    const [rec] = await listRecordings();
    expect(rec.chunkCount).toBe(2);
    expect(rec.finalized).toBe(false);
    expect(await (await assembleBlob(rec.id))!.text()).toBe('onetwo');
  });

  it('keeps the recording when saving it fails', async () => {
    const fake = installFakeMediaRecorder();
    const { result } = renderHook(() =>
      useRecorder(vi.fn(), undefined, {
        onRecording: () => {
          throw new Error('Backend unreachable');
        },
      })
    );

    await act(async () => {
      await result.current.start('audio', { durable: true });
    });
    await act(async () => {
      fake.emit(new Blob(['spoken words']));
      result.current.stop();
      await fake.stop();
    });

    expect(result.current.error).toBe('Backend unreachable');
    // The regression that matters: the audio is still here.
    const stored = await listRecordings();
    expect(stored).toHaveLength(1);
    expect(stored[0].finalized).toBe(true);
    expect(await (await assembleBlob(stored[0].id))!.text()).toBe(
      'spoken words'
    );
  });

  it('flushes the buffer when the page is hidden', async () => {
    const fake = installFakeMediaRecorder();
    const { result } = renderHook(() => useRecorder(vi.fn(), undefined, {}));

    await act(async () => {
      await result.current.start('audio', { durable: true });
    });

    expect(fake.requestDataCalls()).toBe(0);
    await act(async () => {
      hide();
    });
    // Everything captured before the screen locked is now on its way to disk,
    // rather than sitting in a buffer about to be discarded with the page.
    expect(fake.requestDataCalls()).toBe(1);
  });

  it('saves what it has when the OS killed the recorder while hidden', async () => {
    const fake = installFakeMediaRecorder();
    const onRecording = vi.fn();
    const onNotice = vi.fn();
    const { result } = renderHook(() =>
      useRecorder(vi.fn(), undefined, { onRecording, onNotice })
    );

    await act(async () => {
      await result.current.start('audio', { durable: true });
    });
    await act(async () => {
      fake.emit(new Blob(['half a thought']));
      hide();
      // iOS suspends the page; the recorder is dead by the time we come back,
      // and onstop never fires.
      fake.kill();
      show();
    });

    expect(onNotice).toHaveBeenCalledWith(
      expect.stringMatching(/screen was off/i)
    );
    expect(onRecording).toHaveBeenCalledTimes(1);
    const rec = onRecording.mock.calls[0][0];
    expect(rec.finalized).toBe(true);
    expect(rec.recovered).toBe(true);
    expect(await (await assembleBlob(rec.id))!.text()).toBe('half a thought');
  });

  it('stops and keeps the audio when the microphone is taken away', async () => {
    const fake = installFakeMediaRecorder();
    const onRecording = vi.fn();
    const onNotice = vi.fn();
    const { result } = renderHook(() =>
      useRecorder(vi.fn(), undefined, { onRecording, onNotice })
    );

    await act(async () => {
      await result.current.start('audio', { durable: true });
    });
    await act(async () => {
      fake.emit(new Blob(['mid-sentence']));
      // An incoming call ends the track without ending the MediaRecorder.
      fake.endTrack();
      await fake.stop();
    });

    expect(onNotice).toHaveBeenCalledWith(
      expect.stringMatching(/microphone was taken/i)
    );
    expect(onRecording).toHaveBeenCalledTimes(1);
    expect(onRecording.mock.calls[0][0].recovered).toBe(true);
  });

  it('leaves nothing in the store for a non-durable recording', async () => {
    const fake = installFakeMediaRecorder();
    const onAudio = vi.fn();
    const { result } = renderHook(() => useRecorder(vi.fn(), onAudio));

    await act(async () => {
      await result.current.start('audio');
    });
    await act(async () => {
      fake.emit();
      result.current.stop();
      await fake.stop();
    });

    expect(onAudio).toHaveBeenCalledTimes(1);
    expect(await listRecordings()).toEqual([]);
  });
});

function setVisibility(state: 'hidden' | 'visible') {
  Object.defineProperty(document, 'visibilityState', {
    value: state,
    configurable: true,
  });
  document.dispatchEvent(new Event('visibilitychange'));
}
const hide = () => setVisibility('hidden');
const show = () => setVisibility('visible');
