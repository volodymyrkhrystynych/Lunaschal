import { useRef, useState, useEffect } from 'react';

interface Props {
  onTranscribed: (text: string) => void;
}

type Status = 'idle' | 'recording' | 'transcribing';

export function SttPanel({ onTranscribed }: Props) {
  const [status, setStatus] = useState<Status>('idle');
  const [lastText, setLastText] = useState('');
  const [error, setError] = useState('');
  const mediaRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const clearTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (clearTimerRef.current) clearTimeout(clearTimerRef.current);
  }, []);

  const startRecording = async () => {
    setError('');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      chunksRef.current = [];
      const mr = new MediaRecorder(stream);
      mr.ondataavailable = e => { if (e.data.size > 0) chunksRef.current.push(e.data); };
      mr.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());
        setStatus('transcribing');
        try {
          const blob = new Blob(chunksRef.current, { type: 'audio/webm' });
          const fd = new FormData();
          fd.append('audio', blob, 'recording.webm');
          const r = await fetch('/api/transcribe', { method: 'POST', body: fd });
          const data = await r.json();
          if (!r.ok) throw new Error(data.error || 'Transcription failed');
          const text: string = data.text ?? '';
          setLastText(text);
          if (text) onTranscribed(text);
          if (clearTimerRef.current) clearTimeout(clearTimerRef.current);
          clearTimerRef.current = setTimeout(() => setLastText(''), 8000);
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

  const stopRecording = () => {
    mediaRef.current?.stop();
    mediaRef.current = null;
  };

  return (
    <div className="h-10 shrink-0 border-t border-white/10 bg-[var(--color-surface)] flex items-center gap-3 px-4">
      <button
        onClick={status === 'recording' ? stopRecording : startRecording}
        disabled={status === 'transcribing'}
        className={`flex items-center gap-1.5 px-3 py-1 rounded text-sm font-medium transition-colors disabled:opacity-50 ${
          status === 'recording'
            ? 'bg-red-600 hover:bg-red-700 text-white'
            : 'bg-white/10 hover:bg-white/20 text-[var(--color-text)]'
        }`}
      >
        <span className={`w-2 h-2 rounded-full ${
          status === 'recording' ? 'bg-white animate-pulse' :
          status === 'transcribing' ? 'bg-yellow-400' : 'bg-[var(--color-text-muted)]'
        }`} />
        {status === 'recording' ? 'Stop' : status === 'transcribing' ? 'Transcribing…' : 'Record'}
      </button>

      {error && <span className="text-xs text-red-400 truncate">{error}</span>}
      {!error && lastText && (
        <span className="text-xs text-[var(--color-text-muted)] truncate">"{lastText}"</span>
      )}
      {!error && !lastText && status === 'idle' && (
        <span className="text-xs text-[var(--color-text-muted)]">Voice input — transcribes into active editor or clipboard</span>
      )}
    </div>
  );
}
