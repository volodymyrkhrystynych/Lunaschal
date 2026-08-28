import { useEffect, useRef, useState } from 'react';
import { useOnline } from '../offline/useOnline';
import {
  appendChunk,
  beginRecording,
  finalizeRecording,
  type RecordingMode,
  type StoredRecording,
} from '../offline/recordingStore';

export type RecorderStatus = 'idle' | 'recording' | 'transcribing' | 'saving';

/** What happens to the audio once recording stops. */
export type RecorderMode = 'transcribe' | 'audio';

/**
 * How often MediaRecorder hands us a chunk. Anything not yet flushed lives only
 * inside the browser, so this is the width of the window in which a hard kill
 * loses audio — five seconds of a sentence, rather than the whole recording.
 */
export const CHUNK_MS = 5000;

/**
 * Containers to ask MediaRecorder for, best first. iOS Safari supports none of
 * the webm ones and produces audio/mp4; the point of asking is to *know* which
 * we got, because the backend decides an attachment's kind from its mime type
 * (`resolve_upload`) and the old code hardcoded 'audio/webm' regardless.
 */
const PREFERRED_TYPES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/mp4',
  'audio/aac',
];

function pickMimeType(): string | undefined {
  if (typeof MediaRecorder === 'undefined') return undefined;
  if (typeof MediaRecorder.isTypeSupported !== 'function') return undefined;
  return PREFERRED_TYPES.find(t => MediaRecorder.isTypeSupported(t));
}

interface WakeLockSentinelish {
  release: () => Promise<void>;
}
type WakeLockish = {
  request: (type: 'screen') => Promise<WakeLockSentinelish>;
};

function wakeLockApi(): WakeLockish | undefined {
  return (navigator as Navigator & { wakeLock?: WakeLockish }).wakeLock;
}

export interface RecorderOptions {
  /**
   * Persist the audio to IndexedDB as it is recorded, and hand the finished
   * recording's *id* to `onRecording` instead of a Blob. Opt-in: the journal
   * buttons use it because losing one of those loses a journal entry; the
   * throwaway dictation buttons elsewhere keep the old in-memory path.
   */
  durable?: boolean;
  /**
   * Called once the audio is safely stored. Whatever this does — upload,
   * transcribe, queue for later — the recording stays on the device until it
   * deletes it. See src/offline/recordingQueue.ts for the two policies.
   */
  onRecording?: (rec: StoredRecording) => void | Promise<void>;
  /** Something the user should know that isn't an error (e.g. a screen lock
   *  ended the recording early). */
  onNotice?: (message: string) => void;
}

/**
 * Microphone → MediaRecorder → POST /api/transcribe → transcript callback.
 * Extracted from SttPanel so voice answers (Learning) and the STT bar share
 * one recording path.
 *
 * `start('audio')` skips transcription entirely and hands the raw blob to
 * `onAudio` instead — the bottom bar's Record button keeps the recording as a
 * journal attachment, and sending it through speech-to-text first would cost a
 * CPU transcription nobody asked for.
 *
 * In `durable` mode (the journal buttons) the audio is written to IndexedDB
 * every `CHUNK_MS` and the recorder never holds the only copy. That mode also
 * handles the ways a phone ends a recording without being asked: a screen lock
 * suspends the page and kills MediaRecorder, an incoming call revokes the mic,
 * and a backgrounded tab can be discarded outright. Each of those used to be a
 * silent, total loss.
 */
export function useRecorder(
  onTranscript: (text: string) => void,
  onAudio?: (blob: Blob) => void | Promise<void>,
  options: RecorderOptions = {}
) {
  const [status, setStatus] = useState<RecorderStatus>('idle');
  const [error, setError] = useState('');
  const mediaRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const modeRef = useRef<RecorderMode>('transcribe');
  const recordingRef = useRef<StoredRecording | null>(null);
  const recoveredRef = useRef(false);
  const wakeRef = useRef<WakeLockSentinelish | null>(null);
  const mountedRef = useRef(true);
  // Held for the whole of `start()`, including the await on the permission
  // prompt — which is the window a second tap used to slip through.
  const startingRef = useRef(false);

  // Callbacks are read through a ref so the lifecycle listeners below can be
  // installed once and still see the current ones.
  const optionsRef = useRef(options);
  optionsRef.current = options;
  // Same reason, and it matters more than it looks: `mr.onstop` is assigned
  // inside `start()`, so without this the transcript would be handed to the
  // callback as it was *when recording began*. Fine while a callback only did
  // `setInput(prev => …)`, wrong the moment one of them sends the message —
  // it would send against a stale conversation.
  const deliverRef = useRef({ onTranscript, onAudio });
  deliverRef.current = { onTranscript, onAudio };

  // Dictation is the one thing on this hook that genuinely needs the backend:
  // the microphone and the audio work anywhere, but turning speech into text is
  // a server call. So the mic button doubles as the app's offline indicator — a
  // control going flat where you were about to use it says more, and says it
  // where you are looking, than a status bar somewhere else on the screen.
  //
  // `audio` mode is deliberately unaffected: those recordings go to IndexedDB
  // and upload later (src/offline/recordingQueue.ts), so they work offline and
  // must keep working.
  const canTranscribe = useOnline();
  const canTranscribeRef = useRef(canTranscribe);
  canTranscribeRef.current = canTranscribe;

  const setStatusIfMounted = (s: RecorderStatus) => {
    if (mountedRef.current) setStatus(s);
  };
  const setErrorIfMounted = (e: string) => {
    if (mountedRef.current) setError(e);
  };

  const releaseWakeLock = () => {
    const sentinel = wakeRef.current;
    wakeRef.current = null;
    void sentinel?.release().catch(() => undefined);
  };

  const acquireWakeLock = async () => {
    // Keeping the screen on is the actual fix for "it stopped recording by
    // itself": the auto-lock is what suspends the page in the first place.
    // Feature-detected — Safari only got it in 16.4, and it is a nicety, not a
    // requirement (everything below still recovers if it isn't there).
    const api = wakeLockApi();
    if (!api) return;
    try {
      wakeRef.current = await api.request('screen');
    } catch {
      // Denied or unsupported; recording continues without it.
    }
  };

  /** Stop the hardware without going through MediaRecorder's stop(). */
  const releaseStream = () => {
    streamRef.current?.getTracks().forEach(t => t.stop());
    streamRef.current = null;
  };

  /**
   * Whether this recorder's microphone is already gone — in which case `stop()`
   * will throw rather than work, and `onstop` is never coming.
   *
   * `mr.state` alone is not enough to know that: Safari leaves it at
   * 'recording' after the capture ends, so the guard that trusted it fell
   * through to a `stop()` that threw `InvalidStateError`. The track is the only
   * honest signal.
   */
  const captureEnded = (mr: MediaRecorder): boolean => {
    if (mr.state === 'inactive') return true;
    const tracks = (mr.stream ?? streamRef.current)?.getAudioTracks?.() ?? [];
    return tracks.length > 0 && tracks.every(t => t.readyState === 'ended');
  };

  /**
   * Which recorder has already been closed out. A dying microphone fires both
   * our own `onended` handler and MediaRecorder's `onstop`, so without this the
   * second of the two finalizes the recording again and delivers it twice.
   */
  const settledRef = useRef<MediaRecorder | null>(null);

  /** Close out a recording MediaRecorder will not close for us. */
  const finishByHand = (mr: MediaRecorder) => {
    if (settledRef.current === mr) return;
    settledRef.current = mr;
    mediaRef.current = null;
    releaseStream();
    releaseWakeLock();
    const recording = recordingRef.current;
    recordingRef.current = null;
    if (recording) {
      void finalizeRecording(recording.id, { recovered: true }).then(
        finalized => {
          if (finalized) return handleFinishedRecording(finalized);
          setStatusIfMounted('idle');
        }
      );
    } else {
      setStatusIfMounted('idle');
    }
  };

  const handleFinishedRecording = async (rec: StoredRecording) => {
    setStatusIfMounted('saving');
    try {
      await optionsRef.current.onRecording?.(rec);
    } catch (err) {
      setErrorIfMounted(err instanceof Error ? err.message : 'Saving failed');
    } finally {
      setStatusIfMounted('idle');
    }
  };

  const handleFinishedBlob = async (blob: Blob) => {
    const deliverAudio = deliverRef.current.onAudio;
    if (modeRef.current === 'audio' && deliverAudio) {
      setStatusIfMounted('saving');
      try {
        await deliverAudio(blob);
      } catch (err) {
        setErrorIfMounted(err instanceof Error ? err.message : 'Saving failed');
      } finally {
        setStatusIfMounted('idle');
      }
      return;
    }

    setStatusIfMounted('transcribing');
    try {
      const fd = new FormData();
      fd.append('audio', blob, 'recording.webm');
      const r = await fetch('/api/transcribe', {
        method: 'POST',
        body: fd,
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || 'Transcription failed');
      if (data.text) deliverRef.current.onTranscript(data.text);
    } catch (err) {
      setErrorIfMounted(
        err instanceof Error ? err.message : 'Transcription failed'
      );
    } finally {
      setStatusIfMounted('idle');
    }
  };

  const start = async (
    mode: RecorderMode = 'transcribe',
    opts: { durable?: boolean } = {}
  ) => {
    // A second tap while the first start is still waiting on the permission
    // prompt used to open a second recorder over a second getUserMedia: one
    // `mediaRef` and one `chunksRef` between them, so Stop reached only the
    // newer one and the older kept the microphone live with nothing able to
    // release it. On iOS it is worse than a leak — a second capture ends the
    // first outright, which is what killed the track that wedged the button.
    if (startingRef.current || mediaRef.current) return;
    setError('');
    if (mode === 'transcribe' && !canTranscribeRef.current) {
      setError('Dictation needs the server — the mic is back when you are.');
      return;
    }
    modeRef.current = mode;
    recoveredRef.current = false;
    // Per-start, because SttPanel drives three buttons off one recorder and only
    // the two journal ones are worth persisting; the third is dictation into a
    // text field, where a lost take is retyped, not lost.
    const durable = opts.durable ?? optionsRef.current.durable ?? false;
    if (!navigator.mediaDevices?.getUserMedia) {
      setError(
        'Microphone access requires HTTPS on this device — reload the page over the https:// URL.'
      );
      return;
    }
    startingRef.current = true;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      chunksRef.current = [];
      const preferred = pickMimeType();
      const mr = new MediaRecorder(
        stream,
        preferred ? { mimeType: preferred } : undefined
      );
      const mimeType = mr.mimeType || preferred || 'audio/webm';

      // Claimed before anything below can await. The microphone can end at any
      // point during setup, and `t.onended` decides whether the event is still
      // relevant by comparing against this ref — so while it was assigned only
      // after `mr.start()`, a track dying mid-setup compared against `null`,
      // took the early return, and left the UI showing a live recording on a
      // dead microphone with no notice and no error.
      mediaRef.current = mr;

      // Assigned after the store call below, but the handlers that close over
      // it have to be attached before it: during that await the recorder had no
      // `onended` listener at all, so a track ending there was lost outright.
      let recording: StoredRecording | null = null;

      mr.ondataavailable = e => {
        if (e.data.size === 0) return;
        if (!recording) {
          chunksRef.current.push(e.data);
          return;
        }
        // Deliberately not awaited: ondataavailable must return promptly, and
        // recordingStore serializes writes internally so order still holds.
        void appendChunk(recording.id, e.data).catch(err => {
          // Out of quota or IndexedDB refused the write. Stop rather than keep
          // recording into nothing — what is already stored stays usable.
          setErrorIfMounted(
            err instanceof Error
              ? `Storage failed, recording stopped: ${err.message}`
              : 'Storage failed, recording stopped'
          );
          stop();
        });
      };

      mr.onerror = () => {
        setErrorIfMounted('The recording stopped unexpectedly.');
        recoveredRef.current = true;
        stop();
      };

      // An incoming call, or another app taking the mic, ends the track without
      // ending the MediaRecorder.
      stream.getAudioTracks().forEach(t => {
        t.onended = () => {
          if (mediaRef.current !== mr) return;
          recoveredRef.current = true;
          optionsRef.current.onNotice?.(
            'The microphone was taken by something else — the recording so far is saved.'
          );
          stop();
        };
      });

      mr.onstop = async () => {
        // May have been closed out already by `finishByHand` — a track that
        // ends takes both routes here.
        if (settledRef.current === mr) return;
        settledRef.current = mr;
        releaseStream();
        releaseWakeLock();
        mediaRef.current = null;

        if (recording) {
          const finalized = await finalizeRecording(recording.id, {
            recovered: recoveredRef.current,
          });
          recordingRef.current = null;
          if (finalized) await handleFinishedRecording(finalized);
          else setStatusIfMounted('idle');
          return;
        }

        await handleFinishedBlob(
          new Blob(chunksRef.current, { type: mimeType })
        );
      };

      if (durable) {
        // Created before the first chunk so there is somewhere to put it.
        const storedMode: RecordingMode =
          mode === 'audio' ? 'audio' : 'transcribe';
        recording = await beginRecording(storedMode, mimeType);
      }

      // The microphone can die while that write is in flight, in which case the
      // handlers above have already closed this recording out. Starting anyway
      // would put the button back to red over a dead capture.
      if (mediaRef.current !== mr) {
        if (recording)
          void finalizeRecording(recording.id, { recovered: true });
        return;
      }
      recordingRef.current = recording;

      // A timeslice is what makes any of this possible: without one,
      // ondataavailable fires exactly once, at stop, and everything before that
      // is unreachable inside the browser.
      mr.start(CHUNK_MS);
      setStatus('recording');
      if (durable) void acquireWakeLock();
    } catch (err) {
      // The ref is claimed early now, so a failure after that point has to give
      // it back or the guard at the top would refuse every later attempt.
      mediaRef.current = null;
      releaseStream();
      setError(err instanceof Error ? err.message : 'Microphone access denied');
      setStatus('idle');
    } finally {
      startingRef.current = false;
    }
  };

  const stop = () => {
    const mr = mediaRef.current;
    if (!mr) return;
    if (captureEnded(mr)) {
      // Already dead — the OS got there first. onstop won't fire, so finish by
      // hand rather than leaving the recording open forever.
      finishByHand(mr);
      return;
    }
    // The reference is dropped *before* the call, and the call itself is
    // guarded. `mr.stop()` throws InvalidStateError on a recorder that is no
    // longer capturing, and Safari reports a stale `state` that says it still
    // is — so the throw escaped the click handler with the ref still set and
    // the status still 'recording', and every later tap threw in the same
    // place. Nothing moved, and only a reload got the button back.
    mediaRef.current = null;
    try {
      mr.stop();
    } catch {
      finishByHand(mr);
    }
  };

  // The lifecycle handlers. Each of these was previously a silent, total loss.
  useEffect(() => {
    mountedRef.current = true;

    const onVisibility = () => {
      const mr = mediaRef.current;
      if (!mr) return;
      if (document.visibilityState === 'hidden') {
        // About to be suspended (screen lock, app switch). Flush now so at most
        // a fraction of a second is still unwritten.
        try {
          if (mr.state === 'recording') mr.requestData();
        } catch {
          // Not supported / wrong state — the next timeslice covers it.
        }
        return;
      }
      // Back in the foreground. iOS routinely kills the recorder while the page
      // is suspended, and the UI used to come back showing 'recording' as if
      // nothing had happened. Asked through `captureEnded` rather than `state`
      // for the same reason `stop()` does: a suspended iOS page comes back with
      // a dead microphone and a recorder still claiming to be recording.
      if (captureEnded(mr)) {
        recoveredRef.current = true;
        optionsRef.current.onNotice?.(
          'The recording ended while the screen was off — everything up to that point is saved.'
        );
        stop();
      } else if (recordingRef.current && !wakeRef.current) {
        // Wake locks are released on hide; take it again.
        void acquireWakeLock();
      }
    };

    const onPageHide = () => {
      const mr = mediaRef.current;
      if (!mr || mr.state !== 'recording') return;
      // The page is being discarded. Force out the last partial chunk and close
      // the record so the startup sweep finds a complete recording.
      try {
        mr.requestData();
      } catch {
        // Best effort.
      }
      const recording = recordingRef.current;
      if (recording) void finalizeRecording(recording.id, { recovered: true });
    };

    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('pagehide', onPageHide);
    return () => {
      mountedRef.current = false;
      document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('pagehide', onPageHide);
      // Unmounting mid-recording used to leak the MediaRecorder and the mic
      // stream, and onstop never ran — so the audio went nowhere.
      stop();
      releaseStream();
      releaseWakeLock();
    };
  }, []);

  return { status, error, start, stop, canTranscribe };
}
