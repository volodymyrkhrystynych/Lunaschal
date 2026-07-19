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
});
