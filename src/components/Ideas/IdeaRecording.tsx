import type { IdeaRecording as Recording } from '../../hooks/api';

interface IdeaRecordingProps {
  recording: Recording;
  /** Switch to the Journal and scroll to the entry this clip became. */
  onOpenEntry?: (entryId: string) => void;
}

/**
 * The clip an idea was dictated from.
 *
 * The audio is not the idea's — it hangs off the journal entry the same
 * recording created, and this is a pointer at it (`recording` on the detail
 * fetch, resolved server-side). Worth having on this screen anyway: the body
 * text is a transcription of what was said, and when it comes back garbled or
 * empty, the original is the only thing that still holds the idea.
 *
 * It also carries the transcript's state, which is the honest answer to "why is
 * this idea blank" — still running, or failed, with the audio intact either way.
 */
export function IdeaRecording({ recording, onOpenEntry }: IdeaRecordingProps) {
  const status = recording.transcriptStatus;
  return (
    <div className="mx-4 mt-4 rounded border border-white/10 bg-[var(--color-bg)] p-3">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-xs text-[var(--color-text-muted)]">
          🎙️ Recorded
        </span>
        {status === 'running' && (
          <span className="text-xs text-[var(--color-primary)]">
            Transcribing…
          </span>
        )}
        <span className="flex-1" />
        <button
          type="button"
          onClick={() => onOpenEntry?.(recording.entryId)}
          disabled={!onOpenEntry}
          title="This recording is also a journal entry"
          className="px-2 py-0.5 rounded text-xs text-[var(--color-text-muted)] hover:bg-white/10 disabled:opacity-40 disabled:hover:bg-transparent"
        >
          Journal entry ›
        </button>
      </div>

      {recording.url ? (
        <audio src={recording.url} controls preload="none" className="w-full" />
      ) : (
        // The entry survived but its audio did not (deleted from the journal).
        <p className="text-xs text-[var(--color-text-muted)]">
          The recording is no longer attached to that entry.
        </p>
      )}

      {status === 'error' && (
        <p className="mt-2 text-xs text-red-400">
          {recording.transcriptError || 'Transcription failed'} — the recording
          is still here, and the entry can be transcribed again from the
          Journal.
        </p>
      )}
    </div>
  );
}
