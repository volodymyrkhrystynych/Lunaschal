import { useRef, useState } from 'react';

export type RecorderStatus = 'idle' | 'recording' | 'transcribing' | 'saving';

/** What happens to the audio once recording stops. */
export type RecorderMode = 'transcribe' | 'audio';

/**
 * Microphone → MediaRecorder → POST /api/transcribe → transcript callback.
 * Extracted from SttPanel so voice answers (Learning) and the STT bar share
 * one recording path.
 *
 * `start('audio')` skips transcription entirely and hands the raw blob to
 * `onAudio` instead — the bottom bar's Record button keeps the recording as a
 * journal attachment, and sending it through speech-to-text first would cost a
 * CPU transcription nobody asked for.
 */
export function useRecorder(
  onTranscript: (text: string) => void,
  onAudio?: (blob: Blob) => void | Promise<void>
) {
  const [status, setStatus] = useState<RecorderStatus>('idle');
  const [error, setError] = useState('');
  const mediaRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const modeRef = useRef<RecorderMode>('transcribe');

  const start = async (mode: RecorderMode = 'transcribe') => {
    setError('');
    modeRef.current = mode;
    if (!navigator.mediaDevices?.getUserMedia) {
      setError(
        'Microphone access requires HTTPS on this device — reload the page over the https:// URL.'
      );
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      chunksRef.current = [];
      const mr = new MediaRecorder(stream);
      mr.ondataavailable = e => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      mr.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());
        const blob = new Blob(chunksRef.current, { type: 'audio/webm' });

        if (modeRef.current === 'audio' && onAudio) {
          setStatus('saving');
          try {
            await onAudio(blob);
          } catch (err) {
            setError(err instanceof Error ? err.message : 'Saving failed');
          } finally {
            setStatus('idle');
          }
          return;
        }

        setStatus('transcribing');
        try {
          const fd = new FormData();
          fd.append('audio', blob, 'recording.webm');
          const r = await fetch('/api/transcribe', {
            method: 'POST',
            body: fd,
          });
          const data = await r.json();
          if (!r.ok) throw new Error(data.error || 'Transcription failed');
          if (data.text) onTranscript(data.text);
        } catch (err) {
          setError(err instanceof Error ? err.message : 'Transcription failed');
        } finally {
          setStatus('idle');
        }
      };
      mr.start();
      mediaRef.current = mr;
      setStatus('recording');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Microphone access denied');
      setStatus('idle');
    }
  };

  const stop = () => {
    mediaRef.current?.stop();
    mediaRef.current = null;
  };

  return { status, error, start, stop };
}
