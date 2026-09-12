import type { RecorderStatus } from '../hooks/useRecorder';

/** Shared control for audio capture. Callers decide where a finished clip goes. */
export function RecordingButton({
  status,
  starting = false,
  onClick,
  disabled = false,
  action = 'transcribe',
  statusLabel,
  title = 'Record a clip — saved audio is transcribed after sending',
  testId,
  className = '',
}: {
  status: RecorderStatus;
  starting?: boolean;
  onClick: () => void;
  /** Prevent starting; an active recording must always remain stoppable. */
  disabled?: boolean;
  /** Transcribe keeps audio and produces text; dictate only inserts text. */
  action?: 'transcribe' | 'audio' | 'dictate';
  /** Feedback for an external recorder, such as the desktop voice listener. */
  statusLabel?: string;
  title?: string;
  testId?: string;
  /** Layout only: recording colors and feedback belong to this component. */
  className?: string;
}) {
  const label = {
    transcribe: 'Transcribe',
    audio: 'Record',
    dictate: 'Dictate',
  }[action];
  const recording = status === 'recording';
  const busy = !recording && (starting || status !== 'idle');
  const text = recording
    ? 'Stop'
    : starting
      ? 'Starting…'
      : status === 'saving'
        ? 'Saving…'
        : status === 'transcribing'
          ? 'Transcribing…'
          : (statusLabel ?? label);
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={!recording && (disabled || busy)}
      aria-label={recording ? 'Stop recording' : text}
      aria-pressed={recording}
      aria-busy={busy}
      data-recording-state={
        recording ? 'recording' : starting ? 'starting' : status
      }
      data-testid={testId}
      title={recording ? 'Stop recording' : busy ? text : title}
      className={`shrink-0 inline-flex items-center justify-center gap-1.5 px-2 py-1 rounded text-sm font-medium transition-colors disabled:opacity-50 ${
        recording
          ? 'bg-red-600 hover:bg-red-700 text-white'
          : 'bg-white/10 hover:bg-white/20 text-[var(--color-text)]'
      } ${className}`}
    >
      <span aria-hidden="true" className={recording ? 'animate-pulse' : ''}>
        {recording ? '■' : busy ? '◌' : '🎙️'}
      </span>
      <span>{text}</span>
    </button>
  );
}
