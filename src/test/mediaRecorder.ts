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
  /** End the microphone track, as an incoming call does. Chrome gives up on
   *  the recorder too, so `state` follows the track. */
  endTrack(): void;
  /** End the microphone the way Safari does: the capture is gone, but `state`
   *  goes on claiming 'recording' — so anything that trusts `state` decides
   *  stop() is safe to call, and it throws. */
  endTrackLeavingStaleState(): void;
  /** End the microphone with no event at all — it can die between getUserMedia
   *  resolving and the recorder being wired up, where nothing is listening. */
  endTrackSilently(): void;
  state(): string;
  trackStopCalls(): number;
  recorderCount(): number;
  requestDataCalls(): number;
}

export function installFakeMediaRecorder(
  mimeType = 'audio/webm;codecs=opus'
): FakeRecorderControls {
  let current: FakeMediaRecorder | null = null;
  let made = 0;
  const track = {
    kind: 'audio',
    // Tracked because it is the only signal that stays honest when a browser
    // leaves MediaRecorder.state stale — which is what `captureEnded` reads.
    readyState: 'live',
    onended: null as null | (() => void),
    stop: vi.fn(() => {
      track.readyState = 'ended';
    }),
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
    stream: unknown;
    ondataavailable: ((e: { data: Blob }) => void) | null = null;
    onstop: (() => void | Promise<void>) | null = null;
    onerror: (() => void) | null = null;
    requestDataCalls = 0;

    constructor(source?: unknown) {
      this.stream = source ?? stream;
      made += 1;
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
      // What every browser does when asked to stop something that is not
      // recording, and the throw the app used to let escape its click handler.
      if (this.state !== 'recording' || track.readyState === 'ended') {
        throw new DOMException(
          'The MediaRecorder is not recording',
          'InvalidStateError'
        );
      }
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
    endTrack: () => {
      track.readyState = 'ended';
      if (current) current.state = 'inactive';
      track.onended?.();
    },
    endTrackLeavingStaleState: () => {
      track.readyState = 'ended';
      track.onended?.();
    },
    endTrackSilently: () => {
      track.readyState = 'ended';
    },
    state: () => current?.state ?? 'none',
    trackStopCalls: () => track.stop.mock.calls.length,
    recorderCount: () => made,
    requestDataCalls: () => current?.requestDataCalls ?? 0,
  };
}
