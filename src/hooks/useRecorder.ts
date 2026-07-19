import { useRef, useState } from 'react';

export type RecorderStatus = 'idle' | 'recording' | 'transcribing';

/**
 * Microphone → MediaRecorder → POST /api/transcribe → transcript callback.
 * Extracted from SttPanel so voice answers (Learning) and the STT bar share
 * one recording path.
 */
export function useRecorder(onTranscript: (text: string) => void) {
  const [status, setStatus] = useState<RecorderStatus>('idle');
  const [error, setError] = useState('');
  const mediaRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  const start = async () => {
    setError('');
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
        setStatus('transcribing');
        try {
          const blob = new Blob(chunksRef.current, { type: 'audio/webm' });
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
