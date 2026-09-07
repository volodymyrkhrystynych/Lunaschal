import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import {
  headline,
  queueSentence,
  TONE_CLASSES,
  TONE_DOT,
} from '../../lib/inferencePause';

/** The GPU inference switch.
 *
 * Rendered above the Settings tabs rather than inside a CollapsibleSection, on
 * purpose: it is app-wide state that every tab's work depends on, and a control
 * you came to Settings specifically to flip is the wrong thing to put behind a
 * disclosure triangle.
 */
export function InferenceSection() {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ['settings', 'inference'],
    queryFn: api.settings.inference,
    // Follows BackupSection's conditional-poll pattern rather than VRAMSection's
    // unconditional 5s: the only moving part is the router letting go of the
    // model, so watch while it is mid-transition and stop once it settles.
    refetchInterval: (q): number | false => {
      const s = q.state.data as { modelStatus?: string | null } | undefined;
      return s?.modelStatus === 'loading' ? 2000 : false;
    },
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['settings', 'inference'] });
    // The VRAM budget panel is the other half of this story.
    qc.invalidateQueries({ queryKey: ['settings', 'gpu-vram'] });
    qc.invalidateQueries({ queryKey: ['settings'] });
  };

  const pause = useMutation({
    mutationFn: api.settings.pauseInference,
    onSuccess: invalidate,
  });
  const resume = useMutation({
    mutationFn: api.settings.resumeInference,
    onSuccess: invalidate,
  });

  const paused = !!data?.paused;
  const busy = pause.isPending || resume.isPending;
  const state = headline(data);

  return (
    <div
      className={`mb-6 p-4 rounded-lg border ${TONE_CLASSES[state.tone]} bg-[var(--color-surface)]`}
    >
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span
              className={`w-2 h-2 rounded-full shrink-0 ${TONE_DOT[state.tone]}`}
              aria-hidden="true"
            />
            <h3 className="font-medium text-[var(--color-text)]">
              {state.title}
            </h3>
          </div>
          <p className="text-sm text-[var(--color-text-muted)] mt-1">
            {state.detail}
          </p>
          <p className="text-sm text-[var(--color-text-muted)] mt-1">
            {queueSentence(data)}
          </p>
          {/* This panel is deliberately a switch and a sentence; the log that
              says which calls ran, waited or were refused lives one tab over,
              where somebody debugging already looks. */}
          <p className="text-xs text-[var(--color-text-muted)] mt-1">
            What the service has been doing is under Logs → LLM service
            activity.
          </p>
          {data?.unloadError && (
            <p className="text-sm text-red-300 mt-1">
              The router did not answer the unload: {data.unloadError}
            </p>
          )}
        </div>

        <button
          onClick={() => (paused ? resume.mutate() : pause.mutate())}
          disabled={busy}
          aria-pressed={paused}
          className={`px-4 py-2 rounded text-sm font-medium transition-colors shrink-0 disabled:opacity-50 ${
            paused
              ? 'bg-green-600/20 text-green-200 border border-green-500/40 hover:bg-green-600/30'
              : 'bg-amber-600/20 text-amber-200 border border-amber-500/40 hover:bg-amber-600/30'
          }`}
        >
          {busy ? 'Working…' : paused ? 'Resume inference' : 'Pause for gaming'}
        </button>
      </div>

      {!paused && (
        <p className="text-xs text-[var(--color-text-muted)] mt-3">
          Pausing unloads the chat model so the GPU is free. Photo captions,
          audio descriptions and transcription keep working — they run on the
          CPU. Anything that needs the chat model is queued until you resume.
        </p>
      )}
    </div>
  );
}
