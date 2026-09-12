import { RecordingButton } from './RecordingButton';
import { clipLabel } from '../lib/clipStage';
import type { ClipStage } from '../hooks/useClipStage';

/**
 * The strip of clips a composer has staged, plus whatever the recorder had to
 * say.
 *
 * Separate from RecordingButton so composers can position their recording
 * control beside their other actions while keeping clips and notices below.
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
 * Convenience layout; RecordingButton owns all recording feedback and styling.
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
      <RecordingButton
        status={stage.status}
        starting={stage.starting}
        onClick={stage.toggle}
        idleLabel="Record a clip"
        testId={testId}
      />
      <ClipStrip stage={stage} testId={testId} />
    </div>
  );
}
