import { vi } from 'vitest';

/**
 * A MediaRecorder/getUserMedia pair jsdom does not provide.
 *
 * Modelled closely enough to exercise the parts of the API the recorder now
 * depends on for durability — `state`, `requestData()`, a real `mimeType`, and
 * tracks that can end on their own — because those are exactly the paths that
 * only fire on a phone. A fake that ignores them can only prove the happy path,
 * which is the one that was never broken.
 */
export interface FakeRecorderControls {
  /** Deliver one timeslice, as MediaRecorder does every `CHUNK_MS`. */
  emit(data?: Blob): void;
  /** Finish normally, as the user pressing Stop does. */
  stop(): Promise<void>;
  /** End the recording the way iOS does when the page is suspended: the
   *  recorder simply goes inactive, and `onstop` never fires. */
  kill(): void;
  /** End the microphone track, as an incoming call does. */
  endTrack(): void;
  state(): string;
  requestDataCalls(): number;
}

export function installFakeMediaRecorder(
  mimeType = 'audio/webm;codecs=opus'
): FakeRecorderControls {
  let current: FakeMediaRecorder | null = null;
  const track = {
    kind: 'audio',
    onended: null as null | (() => void),
    stop: vi.fn(),
  };
  const stream = {
    getTracks: () => [track],
    getAudioTracks: () => [track],
  };

  Object.defineProperty(navigator, 'mediaDevices', {
    value: { getUserMedia: async () => stream },
    configurable: true,
  });

  class FakeMediaRecorder {
    state = 'inactive';
    mimeType = mimeType;
    ondataavailable: ((e: { data: Blob }) => void) | null = null;
    onstop: (() => void | Promise<void>) | null = null;
    onerror: (() => void) | null = null;
    requestDataCalls = 0;

    constructor() {
      current = this;
    }
    static isTypeSupported(type: string) {
      return type === mimeType;
    }
    start() {
      this.state = 'recording';
    }
    requestData() {
      this.requestDataCalls++;
    }
    stop() {
      // `onstop` is fired by the control's `stop()` instead, so a test can await
      // the async work the handler kicks off.
      this.state = 'inactive';
    }
  }

  vi.stubGlobal('MediaRecorder', FakeMediaRecorder);

  return {
    emit: (data = new Blob(['chunk'], { type: mimeType })) =>
      current?.ondataavailable?.({ data }),
    stop: async () => {
      await current?.onstop?.();
    },
    kill: () => {
      if (current) current.state = 'inactive';
    },
    endTrack: () => track.onended?.(),
    state: () => current?.state ?? 'none',
    requestDataCalls: () => current?.requestDataCalls ?? 0,
  };
}
