import { clipButtonLabel, clipLabel } from '../lib/clipStage';
import type { ClipStage } from '../hooks/useClipStage';

/**
 * The strip of clips a composer has staged, plus whatever the recorder had to
 * say.
 *
 * Split from the button below because Food's microphone is a round one sitting
 * inside its textarea, while the other three composers want a labelled button
 * in a row of controls. What must not vary between them is *this* — how a
 * staged clip is shown, that it can be thrown away, and that the recorder's
 * notices reach the user.
 */
export function ClipStrip({
  stage,
  testId,
}: {
  stage: ClipStage;
  testId?: string;
}) {
  return (
    <>
      {stage.clips.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-2">
          {stage.clips.map((clip, i) => (
            <span
              key={clip.id}
              data-testid={testId ? `${testId}-clip` : undefined}
              className="inline-flex items-center gap-1.5 px-2 py-1 text-xs rounded bg-white/5 border border-white/10 text-[var(--color-text-muted)]"
            >
              <span aria-hidden="true">🎙️</span>
              {/* Its own element holding one text node: the icon beside it
                  would otherwise be part of the label as far as anything
                  reading the DOM is concerned. */}
              <span>{clipLabel(i, clip)}</span>
              <button
                type="button"
                onClick={() => stage.remove(clip.id)}
                aria-label={`Discard ${clipLabel(i, clip)}`}
                className="text-[var(--color-text-muted)] hover:text-red-400"
              >
                ✕
              </button>
            </span>
          ))}
        </div>
      )}

      {stage.error && (
        <p className="mt-2 text-xs text-red-400">{stage.error}</p>
      )}
      {!stage.error && stage.notice && (
        <p className="mt-2 text-xs text-amber-300">{stage.notice}</p>
      )}
    </>
  );
}

/**
 * A composer's record button with its staged clips underneath.
 *
 * One component for the three composers that want a labelled button (the
 * Journal composer, the fanfic reader's commentary box, Ideas capture) because
 * they used to be three hand-rolled buttons that disagreed about the glyph,
 * the labels, which states disabled them and whether being offline blocked
 * recording at all — and the last of those was a real bug: recording works
 * offline, the clip is stored and uploaded later, so a mic greyed out on a bad
 * connection was refusing to do something it could do.
 */
export function ClipRecorder({
  stage,
  testId,
  className = '',
}: {
  stage: ClipStage;
  testId?: string;
  className?: string;
}) {
  return (
    <div className={className}>
      <button
        type="button"
        onClick={stage.toggle}
        // Only while a clip is being closed out. Recording itself needs no
        // backend, and while recording this button is the only way to stop.
        disabled={stage.busy}
        data-testid={testId}
        aria-label={stage.recording ? 'Stop recording' : 'Record a clip'}
        title="Record — the clip is kept and transcribed after you send"
        className={`px-2 py-1 text-xs rounded border ${
          stage.recording
            ? 'border-red-500/40 bg-red-500/25 text-red-300'
            : 'border-white/10 text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-white/20'
        } disabled:opacity-50`}
      >
        {clipButtonLabel(stage.status, stage.starting)}
      </button>
      <ClipStrip stage={stage} testId={testId} />
    </div>
  );
}
