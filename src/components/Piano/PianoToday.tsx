import { useEffect, useState } from 'react';
import { api } from '../../hooks/api';
import type {
  PianoDailyExercise,
  PianoHistoryDay,
  PianoPreferences,
  PianoToday as PianoTodayData,
} from '../../lib/piano';

export function PianoToday(props: {
  onPractice: (exercise: PianoDailyExercise) => Promise<void>;
  onRepertoire: (exercise: PianoDailyExercise) => Promise<void>;
}) {
  const [today, setToday] = useState<PianoTodayData | null>(null);
  const [draft, setDraft] = useState<PianoPreferences | null>(null);
  const [rating, setRating] = useState<Record<string, number>>({});
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [history, setHistory] = useState<PianoHistoryDay[]>([]);

  const refresh = async () => {
    try {
      const value = await api.piano.today();
      setToday(value);
      setDraft(value.preferences);
      setHistory(await api.piano.history());
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : 'Could not load today’s routine.'
      );
    }
  };

  useEffect(() => void refresh(), []);

  const savePreferences = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      await api.piano.updatePreferences(draft);
      await refresh();
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : 'Could not save practice settings.'
      );
    } finally {
      setSaving(false);
    }
  };

  const complete = async (exercise: PianoDailyExercise) => {
    try {
      await api.piano.completeExercise(exercise.id, {
        selfRating: rating[exercise.id] ?? 3,
      });
      await refresh();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : 'Could not save the attempt.'
      );
    }
  };

  if (!today || !draft) {
    return (
      <p className="text-[var(--color-text-muted)]">
        Loading today’s practice…
      </p>
    );
  }

  const completed = today.exercises.filter(item => item.completedAt).length;
  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <header className="rounded-lg border border-white/10 bg-[var(--color-surface)] p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h3 className="text-2xl font-semibold">Today’s practice</h3>
            <p className="mt-1 text-sm text-[var(--color-text-muted)]">
              {today.dayKey} · {completed} of {today.exercises.length} complete
            </p>
          </div>
          <div className="text-right">
            <div className="text-2xl font-semibold">
              {draft.sessionMinutes} min
            </div>
            <div className="text-xs text-[var(--color-text-muted)]">
              planned session
            </div>
          </div>
        </div>
        <div className="mt-4 h-2 overflow-hidden rounded bg-white/10">
          <div
            className="h-full bg-emerald-500 transition-all"
            style={{
              width: `${today.exercises.length ? (completed / today.exercises.length) * 100 : 0}%`,
            }}
          />
        </div>
        <details className="mt-5">
          <summary className="cursor-pointer text-sm font-medium">
            Routine settings
          </summary>
          <div className="mt-3 flex flex-wrap items-end gap-4">
            <label className="text-sm">
              Session
              <select
                value={draft.sessionMinutes}
                onChange={event =>
                  setDraft({
                    ...draft,
                    sessionMinutes: Number(event.target.value),
                  })
                }
                className="ml-2 rounded border border-white/20 bg-[var(--color-bg)] px-2 py-1"
              >
                {[20, 25, 30, 45, 60].map(value => (
                  <option key={value} value={value}>
                    {value} min
                  </option>
                ))}
              </select>
            </label>
            <label className="text-sm">
              Level
              <select
                value={draft.skillLevel}
                onChange={event =>
                  setDraft({
                    ...draft,
                    skillLevel: event.target
                      .value as PianoPreferences['skillLevel'],
                  })
                }
                className="ml-2 rounded border border-white/20 bg-[var(--color-bg)] px-2 py-1"
              >
                <option value="beginner">Beginner</option>
                <option value="intermediate">Intermediate</option>
                <option value="advanced">Advanced</option>
              </select>
            </label>
            <label className="text-sm">
              Jazz {draft.jazzPercent}%
              <input
                aria-label="Jazz balance"
                type="range"
                min="0"
                max="100"
                step="25"
                value={draft.jazzPercent}
                onChange={event =>
                  setDraft({
                    ...draft,
                    jazzPercent: Number(event.target.value),
                  })
                }
                className="ml-2 align-middle"
              />
            </label>
            <button
              type="button"
              disabled={saving}
              onClick={() => void savePreferences()}
              className="rounded bg-[var(--color-primary)] px-3 py-1.5 text-sm text-white disabled:opacity-50"
            >
              Save for future days
            </button>
          </div>
          <p className="mt-2 text-xs text-[var(--color-text-muted)]">
            Today’s routine stays stable; changes shape the next day generated.
          </p>
        </details>
      </header>

      {error && (
        <div
          role="alert"
          className="rounded border border-red-500/50 bg-red-500/10 p-3 text-red-300"
        >
          {error}
        </div>
      )}
      <div className="grid gap-4 md:grid-cols-2">
        {today.exercises.map(exercise => (
          <article
            key={exercise.id}
            className={`rounded-lg border p-4 ${exercise.completedAt ? 'border-emerald-500/40 bg-emerald-500/5' : 'border-white/10 bg-[var(--color-surface)]'}`}
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-xs uppercase tracking-wide text-cyan-300">
                  {exercise.category} · {exercise.style}
                </p>
                <h4 className="mt-1 text-lg font-semibold">{exercise.title}</h4>
              </div>
              <span className="rounded bg-white/10 px-2 py-1 text-xs">
                {exercise.minutes} min
              </span>
            </div>
            <p className="mt-2 text-sm">{exercise.description}</p>
            <p className="mt-2 text-sm text-[var(--color-text-muted)]">
              {exercise.instructions}
            </p>
            {(exercise.keyName || exercise.targetTempo) && (
              <p className="mt-3 text-sm">
                <strong>{exercise.keyName}</strong>
                {exercise.targetTempo ? ` · ♩ = ${exercise.targetTempo}` : ''}
              </p>
            )}
            {exercise.completedAt ? (
              <p className="mt-4 text-sm text-emerald-300">
                ✓ Complete
                {exercise.latestAttempt?.selfRating
                  ? ` · ${exercise.latestAttempt.selfRating}/5`
                  : ''}
              </p>
            ) : exercise.exerciseKey === 'repertoire' ? (
              <button
                type="button"
                onClick={() => void props.onRepertoire(exercise)}
                className="mt-4 rounded bg-[var(--color-primary)] px-3 py-2 text-sm text-white"
              >
                Open {exercise.pieceTitle ?? 'piece'}
              </button>
            ) : exercise.gradeable ? (
              <button
                type="button"
                onClick={() => void props.onPractice(exercise)}
                className="mt-4 rounded bg-[var(--color-primary)] px-3 py-2 text-sm text-white"
              >
                Practice with MIDI
              </button>
            ) : (
              <div className="mt-4 flex flex-wrap items-center gap-2">
                <label className="text-sm">
                  How did it feel?
                  <select
                    aria-label={`Rating for ${exercise.title}`}
                    value={rating[exercise.id] ?? 3}
                    onChange={event =>
                      setRating({
                        ...rating,
                        [exercise.id]: Number(event.target.value),
                      })
                    }
                    className="ml-2 rounded border border-white/20 bg-[var(--color-bg)] px-2 py-1"
                  >
                    {[1, 2, 3, 4, 5].map(value => (
                      <option key={value} value={value}>
                        {value}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  onClick={() => void complete(exercise)}
                  className="rounded border border-emerald-500/50 px-3 py-1.5 text-sm text-emerald-300"
                >
                  Mark complete
                </button>
              </div>
            )}
          </article>
        ))}
      </div>
      <details className="rounded-lg border border-white/10 bg-[var(--color-surface)] p-4">
        <summary className="cursor-pointer font-medium">
          Practice history
        </summary>
        <div className="mt-3 space-y-2 text-sm">
          {history.map(day => (
            <div
              key={day.dayKey}
              className="flex justify-between border-t border-white/10 pt-2"
            >
              <span>{day.dayKey}</span>
              <span className="text-[var(--color-text-muted)]">
                {day.completedCount}/{day.exerciseCount} complete ·{' '}
                {day.minutesPlanned} min planned
              </span>
            </div>
          ))}
        </div>
      </details>
    </div>
  );
}
