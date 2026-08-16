import { useState } from 'react';
import { useRecorder } from '@/hooks/useRecorder';

interface Props {
  steer: string;
  onSteerChange: (steer: string) => void;
  /** Runs the fill/tailor with whatever steer is currently set. */
  onRun: (steer: string) => void;
  busy?: boolean;
  runLabel: string;
}

/**
 * The two-stage control.
 *
 * Tap the mic and you dictate how you want this one answered before it runs.
 * Tap the action button and it runs with whatever steer is already stored —
 * which after the first time is usually the right answer, and costs nothing.
 *
 * Dictation reuses the same `useRecorder` every other view uses (getUserMedia →
 * MediaRecorder → POST /api/transcribe), so it works over Tailscale HTTPS on a
 * phone with no extra plumbing.
 */
export function SteerBar({
  steer,
  onSteerChange,
  onRun,
  busy = false,
  runLabel,
}: Props) {
  const [expanded, setExpanded] = useState(false);

  // Dictation appends, so a second pass adds to the instruction rather than
  // replacing what was already said.
  const recorder = useRecorder(text => {
    const next = steer ? `${steer.trim()} ${text.trim()}` : text.trim();
    onSteerChange(next);
    setExpanded(true);
  });

  const recording = recorder.status === 'recording';
  const transcribing = recorder.status === 'transcribing';

  return (
    <div className="rounded-lg border border-white/10 bg-[var(--color-surface)]">
      <div className="flex items-center gap-2 p-2">
        <button
          type="button"
          onClick={() => (recording ? recorder.stop() : void recorder.start())}
          disabled={busy || transcribing}
          aria-label={recording ? 'Stop dictating' : 'Dictate how to answer'}
          className={`min-h-[44px] min-w-[44px] px-3 rounded text-lg transition-colors disabled:opacity-50 ${
            recording
              ? 'bg-red-500/20 text-red-300 border border-red-500/40'
              : 'border border-white/20 bg-white/5 hover:bg-white/10'
          }`}
        >
          {recording ? '■' : '🎤'}
        </button>

        <button
          type="button"
          onClick={() => onRun(steer)}
          disabled={busy || recording}
          className="flex-1 min-h-[44px] px-4 rounded text-sm font-medium bg-[var(--color-primary)]/20 text-[var(--color-primary)] border border-[var(--color-primary)]/40 hover:bg-[var(--color-primary)]/30 disabled:opacity-50"
        >
          {busy ? 'Working…' : runLabel}
        </button>

        <button
          type="button"
          onClick={() => setExpanded(v => !v)}
          aria-label={expanded ? 'Hide instructions' : 'Edit instructions'}
          className="min-h-[44px] min-w-[44px] px-3 rounded border border-white/20 bg-white/5 hover:bg-white/10 text-sm"
        >
          {expanded ? '▲' : '✎'}
        </button>
      </div>

      {(transcribing || recorder.error) && (
        <p className="px-3 pb-2 text-xs text-[var(--color-text-muted)]">
          {recorder.error || 'Transcribing…'}
        </p>
      )}

      {expanded && (
        <div className="px-2 pb-2">
          <textarea
            value={steer}
            onChange={e => onSteerChange(e.target.value)}
            rows={3}
            placeholder="How should this be answered? e.g. “emphasise the payments work, keep it short”"
            className="w-full p-2 rounded bg-[var(--color-bg)] border border-white/10 text-sm text-[var(--color-text)] resize-y"
          />
          {steer && (
            <button
              type="button"
              onClick={() => onSteerChange('')}
              className="mt-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            >
              Clear
            </button>
          )}
        </div>
      )}

      {!expanded && steer && (
        <p className="px-3 pb-2 text-xs text-[var(--color-text-muted)] truncate">
          Steer: {steer}
        </p>
      )}
    </div>
  );
}
