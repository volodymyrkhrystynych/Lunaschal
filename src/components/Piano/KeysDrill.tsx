import type { ReactNode } from 'react';
import type { PianoDailyExercise, PracticeStep } from '../../lib/piano';
import {
  CLEAN_RUNS_REQUIRED,
  formatSpent,
  streakDots,
  type DrillOutcome,
} from '../../lib/pianoDrill';
import { FallingNotes } from './FallingNotes';
import { PianoKeyboard } from './PianoKeyboard';

export interface DrillRunResult {
  wrongNotes: number;
  outcome: DrillOutcome;
}

interface Props {
  exercise: PianoDailyExercise | null;
  position: number;
  total: number;
  streak: number;
  practicedSeconds: number;
  steps: PracticeStep[];
  stepIndex: number;
  tempo: number;
  timelineStartMs: number | null;
  activeNotes: ReadonlySet<number>;
  wrongNotes: number;
  countingIn: boolean;
  practicing: boolean;
  paused: boolean;
  loading: boolean;
  finished: boolean;
  connected: boolean;
  runResult: DrillRunResult | null;
  error: string | null;
  midiControls: ReactNode;
  onPause: () => void;
  onResume: () => void;
  onExit: () => void;
}

export function KeysDrill(props: Props) {
  const {
    exercise,
    runResult,
    paused,
    practicing,
    countingIn,
    finished,
    connected,
  } = props;

  const status = finished
    ? 'Block complete'
    : paused
      ? 'Paused'
      : runResult
        ? runResult.outcome === 'mastered'
          ? 'Three clean in a row — next exercise'
          : runResult.outcome === 'timeUp'
            ? 'Time’s up on this one — next exercise'
            : runResult.wrongNotes === 0
              ? 'Clean run'
              : `${runResult.wrongNotes} wrong ${runResult.wrongNotes === 1 ? 'note' : 'notes'} — again`
        : countingIn
          ? 'Count-in…'
          : practicing
            ? 'Your turn'
            : props.loading
              ? 'Preparing notes…'
              : connected
                ? 'Ready'
                : 'Connect a MIDI keyboard to begin';

  const tone = runResult
    ? runResult.outcome === 'repeat' && runResult.wrongNotes > 0
      ? 'border-amber-400/40 bg-amber-400/10 text-amber-200'
      : 'border-emerald-400/40 bg-emerald-400/10 text-emerald-200'
    : 'border-cyan-400/30 bg-zinc-950/95';

  return (
    <>
      <div
        className={`flex flex-wrap items-center gap-x-5 gap-y-2 rounded-lg border p-3 ${tone}`}
      >
        <div className="mr-auto min-w-0">
          <p className="text-xs uppercase tracking-wide text-cyan-300">
            Exercise {Math.min(props.position + 1, props.total)} of{' '}
            {props.total}
            {exercise ? ` · ${exercise.category}` : ''}
          </p>
          <h3 className="truncate text-lg font-semibold">
            {exercise?.title ?? 'Practice'}
            {exercise?.keyName ? ` in ${exercise.keyName}` : ''}
          </h3>
        </div>
        <span
          aria-label={`${props.streak} of ${CLEAN_RUNS_REQUIRED} clean runs`}
          title="Clean runs in a row"
          className="text-lg tracking-[0.2em] text-emerald-300"
        >
          {streakDots(props.streak)}
        </span>
        {exercise && (
          <span className="text-sm text-[var(--color-text-muted)]">
            {formatSpent(props.practicedSeconds, exercise.minutes)}
            {exercise.targetTempo ? ` · ♩ = ${props.tempo}` : ''}
          </span>
        )}
        <strong className="text-sm">{status}</strong>
        <span className="text-sm text-[var(--color-text-muted)]">
          Wrong notes {props.wrongNotes}
        </span>
        {!finished &&
          (paused ? (
            <button
              type="button"
              onClick={props.onResume}
              disabled={!connected}
              className="rounded bg-emerald-600 px-3 py-1.5 text-sm text-white disabled:opacity-40"
            >
              Resume
            </button>
          ) : (
            <button
              type="button"
              onClick={props.onPause}
              className="rounded border border-white/20 px-3 py-1.5 text-sm"
            >
              Pause
            </button>
          ))}
        <button
          type="button"
          onClick={props.onExit}
          className="rounded border border-white/20 px-3 py-1.5 text-sm"
        >
          {finished ? 'Back to today' : 'Exit'}
        </button>
      </div>

      {props.error && (
        <div
          role="alert"
          className="rounded border border-red-500/50 bg-red-500/10 p-3 text-red-300"
        >
          {props.error}
        </div>
      )}
      {!connected && props.midiControls}

      {finished ? (
        <div className="grid flex-1 place-items-center rounded-lg border border-emerald-500/40 bg-emerald-500/5 p-12 text-center">
          <div>
            <p className="text-xl font-semibold text-emerald-300">
              Practice block done
            </p>
            <p className="mt-2 text-sm text-[var(--color-text-muted)]">
              Every exercise was either played three times clean or given its
              full time.
            </p>
          </div>
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col overflow-x-auto rounded-xl shadow-2xl shadow-black/30">
          <div className="min-h-0 flex-1">
            <FallingNotes
              steps={props.steps}
              stepIndex={props.stepIndex}
              hand="both"
              tempo={props.tempo}
              timelineStartMs={props.timelineStartMs}
            />
          </div>
          <PianoKeyboard activeNotes={props.activeNotes} />
        </div>
      )}
    </>
  );
}
