// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useRecorder } from './useRecorder';

describe('useRecorder', () => {
  const originalMediaDevices = navigator.mediaDevices;

  afterEach(() => {
    Object.defineProperty(navigator, 'mediaDevices', {
      value: originalMediaDevices,
      configurable: true,
    });
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
    const { stop } = fakeMediaRecorder();
    const fetchSpy = vi.spyOn(globalThis, 'fetch');

    const onTranscript = vi.fn();
    const onAudio = vi.fn();
    const { result } = renderHook(() => useRecorder(onTranscript, onAudio));

    await act(async () => {
      await result.current.start('audio');
    });
    expect(result.current.status).toBe('recording');

    await act(async () => {
      result.current.stop();
      await stop();
    });

    expect(onAudio).toHaveBeenCalledTimes(1);
    expect(onAudio.mock.calls[0][0]).toBeInstanceOf(Blob);
    expect(onTranscript).not.toHaveBeenCalled();
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(result.current.status).toBe('idle');
  });

  it('a failed save surfaces on the recorder rather than disappearing', async () => {
    const { stop } = fakeMediaRecorder();
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
      await stop();
    });

    expect(result.current.error).toBe('Disk full');
    expect(result.current.status).toBe('idle');
  });
});

/** A MediaRecorder/getUserMedia pair jsdom does not provide. */
function fakeMediaRecorder() {
  let onstop: (() => Promise<void>) | null = null;
  Object.defineProperty(navigator, 'mediaDevices', {
    value: { getUserMedia: async () => ({ getTracks: () => [] }) },
    configurable: true,
  });
  class FakeMediaRecorder {
    ondataavailable: ((e: { data: Blob }) => void) | null = null;
    set onstop(fn: () => Promise<void>) {
      onstop = fn;
    }
    start() {}
    stop() {}
  }
  vi.stubGlobal('MediaRecorder', FakeMediaRecorder);
  return { stop: () => onstop!() };
}
