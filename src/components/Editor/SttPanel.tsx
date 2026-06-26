import { useRef, useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../hooks/api';

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

  const { data: listenerState } = useQuery({
    queryKey: ['stt', 'listener-state'],
    queryFn: api.stt.listenerState,
    refetchInterval: 500,
  });

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

  // Merge local button state with listener shortcut state.
  // Local state takes precedence when the user has pressed the in-app button.
  const listenerRecording    = listenerState?.recording    ?? false;
  const listenerTranscribing = listenerState?.transcribing ?? false;
  const listenerMode         = listenerState?.mode         ?? null;
  const isListenerActive     = listenerRecording || listenerTranscribing;
  const effectiveStatus: Status =
    status !== 'idle'      ? status :
    listenerRecording      ? 'recording' :
    listenerTranscribing   ? 'transcribing' :
    'idle';

  // When the listener is controlling, the Stop button can't stop the listener
  const isListenerControlling = isListenerActive && status === 'idle';
  const isJournalMode = isListenerControlling && listenerMode === 'journal';
  const buttonDisabled =
    effectiveStatus === 'transcribing' ||
    isListenerControlling;

  const buttonLabel =
    effectiveStatus === 'recording'    ? (isJournalMode ? 'Journal…' : isListenerControlling ? 'Recording…' : 'Stop') :
    effectiveStatus === 'transcribing' ? (isJournalMode ? 'Saving journal…' : 'Transcribing…') :
    'Record';

  return (
    <div className="h-10 shrink-0 border-t border-white/10 bg-[var(--color-surface)] flex items-center gap-3 px-4">
      <button
        onClick={effectiveStatus === 'recording' && !isListenerControlling ? stopRecording : startRecording}
        disabled={buttonDisabled}
        className={`flex items-center gap-1.5 px-3 py-1 rounded text-sm font-medium transition-colors disabled:opacity-50 ${
          effectiveStatus === 'recording' && isJournalMode
            ? 'bg-amber-600 hover:bg-amber-700 text-white'
            : effectiveStatus === 'recording'
            ? 'bg-red-600 hover:bg-red-700 text-white'
            : 'bg-white/10 hover:bg-white/20 text-[var(--color-text)]'
        }`}
      >
        <span className={`w-2 h-2 rounded-full ${
          effectiveStatus === 'recording' && isJournalMode ? 'bg-white animate-pulse' :
          effectiveStatus === 'recording'                  ? 'bg-white animate-pulse' :
          effectiveStatus === 'transcribing'               ? 'bg-yellow-400' :
          'bg-[var(--color-text-muted)]'
        }`} />
        {buttonLabel}
      </button>

      {error && <span className="text-xs text-red-400 truncate">{error}</span>}
      {!error && lastText && (
        <span className="text-xs text-[var(--color-text-muted)] truncate">"{lastText}"</span>
      )}
      {!error && !lastText && effectiveStatus === 'idle' && (
        <span className="text-xs text-[var(--color-text-muted)]">Voice input — transcribes into active editor or clipboard</span>
      )}
    </div>
  );
}
