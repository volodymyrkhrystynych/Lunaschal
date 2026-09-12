// @vitest-environment jsdom
import { act, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SttPanel } from './SttPanel';
import { installFakeMediaRecorder } from '../../test/mediaRecorder';
import { handleFinishedRecording } from '../../offline/recordingQueue';

vi.mock('../../offline/recordingQueue', () => ({
  handleFinishedRecording: vi.fn().mockResolvedValue(undefined),
}));
vi.mock('./PendingRecordings', () => ({ PendingRecordings: () => null }));
vi.mock('../../hooks/api', () => ({
  api: {
    stt: {
      listenerState: vi
        .fn()
        .mockResolvedValue({ recording: false, transcribing: false }),
    },
  },
}));

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe('bottom bar with the real recorder', () => {
  it.each([
    ['Transcribe', 'transcribe'],
    ['Record', 'audio'],
  ])(
    '%s reports startup, capture and stop on the same button',
    async (label, mode) => {
      const fake = installFakeMediaRecorder();
      const getUserMedia = navigator.mediaDevices.getUserMedia.bind(
        navigator.mediaDevices
      );
      let allow!: () => void;
      const permission = new Promise<void>(resolve => {
        allow = resolve;
      });
      vi.spyOn(navigator.mediaDevices, 'getUserMedia').mockImplementation(
        async constraints => {
          await permission;
          return getUserMedia(constraints);
        }
      );
      const onTranscribed = vi.fn();
      render(
        <QueryClientProvider client={new QueryClient()}>
          <SttPanel onTranscribed={onTranscribed} onMeetingUploaded={vi.fn()} />
        </QueryClientProvider>
      );
      const button = screen.getByRole('button', {
        name: label,
      }) as HTMLButtonElement;
      fireEvent.click(button);
      expect(screen.getByRole('button', { name: 'Starting…' })).toBe(button);
      expect(button.disabled).toBe(true);
      const other = screen.getByRole('button', {
        name: label === 'Transcribe' ? 'Record' : 'Transcribe',
      }) as HTMLButtonElement;
      expect(other.disabled).toBe(true);
      allow();
      expect(
        await screen.findByRole('button', { name: 'Stop recording' })
      ).toBe(button);
      expect(button.getAttribute('aria-pressed')).toBe('true');
      expect(other.disabled).toBe(true);
      fake.emit();
      fireEvent.click(button);
      expect(screen.getByRole('button', { name: 'Saving…' })).toBe(button);
      await act(async () => {
        await fake.stop();
      });
      expect(button.getAttribute('data-recording-state')).toBe('idle');
      expect(other.disabled).toBe(false);
      expect(handleFinishedRecording).toHaveBeenCalledWith(
        expect.anything(),
        expect.objectContaining({ mode })
      );
      expect(onTranscribed).not.toHaveBeenCalled();
    }
  );
});
