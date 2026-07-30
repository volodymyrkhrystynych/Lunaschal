import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/hooks/api';
import type { ActivityTypeId, WorkoutSession } from '@/hooks/api';
import {
  ACTIVITY_COLORS,
  ACTIVITY_LABELS,
  ACTIVITY_TYPES,
  isActivityType,
} from '@/lib/lifestyle';
import {
  clearWorkoutDraft,
  DRAFT_SAVE_DELAY_MS,
  EMPTY_DRAFT,
  isDraftEmpty,
  loadWorkoutDraft,
  saveWorkoutDraft,
  type WorkoutDraft,
} from '@/lib/workoutDraft';

const PLACEHOLDER = 'bicep curls 20,10 20,10\nsquats 60,8 60,8 65,6';

function numberOrNull(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const n = Number(trimmed);
  return Number.isFinite(n) ? n : null;
}

function SessionCard({ session }: { session: WorkoutSession }) {
  const queryClient = useQueryClient();
  const [showRaw, setShowRaw] = useState(false);
  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ['lifestyle'] });

  const reparse = useMutation({
    mutationFn: () => api.lifestyle.workouts.reparse(session.id),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: () => api.lifestyle.workouts.delete(session.id),
    onSuccess: invalidate,
  });

  const meta = [
    session.durationMinutes ? `${session.durationMinutes} min` : null,
    session.intensityRating ? `RPE ${session.intensityRating}` : null,
  ].filter(Boolean);

  return (
    <li className="p-3 rounded-lg bg-[var(--color-bg)] border border-white/10">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2 min-w-0">
          <span
            className="inline-block w-2.5 h-2.5 rounded-sm shrink-0"
            style={{ background: ACTIVITY_COLORS[session.locationType] }}
            aria-hidden="true"
          />
          <span className="text-sm text-[var(--color-text)] truncate">
            {session.date} · {ACTIVITY_LABELS[session.locationType]}
          </span>
          {meta.length > 0 && (
            <span className="text-xs text-[var(--color-text-muted)]">
              {meta.join(' · ')}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 text-xs">
          {session.rawText && (
            <button
              type="button"
              onClick={() => setShowRaw(v => !v)}
              className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            >
              {showRaw ? 'Hide raw' : 'Raw'}
            </button>
          )}
          <button
            type="button"
            onClick={() => remove.mutate()}
            className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
          >
            Delete
          </button>
        </div>
      </div>

      {session.parseStatus === 'pending' && (
        <div className="mt-2 text-xs text-[var(--color-text-muted)]">
          Parsing exercises…
        </div>
      )}
      {session.parseStatus === 'error' && (
        // The raw text is untouched, so this is always retryable.
        <div className="mt-2 flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
          Couldn&apos;t parse the exercises — the text above is safe.
          <button
            type="button"
            onClick={() => reparse.mutate()}
            disabled={reparse.isPending}
            className="px-2 py-0.5 rounded border border-white/10 hover:border-[var(--color-primary)] text-[var(--color-text)]"
          >
            {reparse.isPending ? 'Retrying…' : 'Retry'}
          </button>
        </div>
      )}

      {session.exercises.length > 0 && (
        <ul className="mt-2 space-y-1">
          {session.exercises.map(ex => (
            <li key={ex.id} className="text-sm">
              <span className="text-[var(--color-text)]">{ex.displayName}</span>{' '}
              <span className="text-xs text-[var(--color-text-muted)]">
                {ex.sets
                  .map(s =>
                    s.weight != null
                      ? `${s.weight}×${s.reps ?? '?'}`
                      : `${s.reps ?? '?'} reps`
                  )
                  .join('  ')}
              </span>
            </li>
          ))}
        </ul>
      )}

      {showRaw && session.rawText && (
        <pre className="mt-2 p-2 rounded bg-black/20 text-xs text-[var(--color-text-muted)] whitespace-pre-wrap">
          {session.rawText}
        </pre>
      )}
      {session.notes && (
        <p className="mt-2 text-xs text-[var(--color-text-muted)] whitespace-pre-wrap">
          {session.notes}
        </p>
      )}
    </li>
  );
}

/**
 * Freeform workout entry plus recent history.
 *
 * The form mirrors itself into localStorage on a short debounce, so the
 * textarea is never empty after a phone reloads a backgrounded tab mid-session
 * — the case the doc calls a baseline requirement, not a nice-to-have. Nothing
 * here waits on the network: the draft survives losing signal in a basement gym.
 */
export function WorkoutLog() {
  const queryClient = useQueryClient();
  // Read the draft synchronously on mount so a restored session never flashes
  // an empty textarea first.
  const [draft, setDraft] = useState<WorkoutDraft>(
    () => loadWorkoutDraft() ?? EMPTY_DRAFT
  );
  const [restored] = useState(() => loadWorkoutDraft() !== null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const timer = setTimeout(
      () => saveWorkoutDraft(draft),
      DRAFT_SAVE_DELAY_MS
    );
    return () => clearTimeout(timer);
  }, [draft]);

  const { data: sessions = [] } = useQuery({
    queryKey: ['lifestyle', 'workouts'],
    queryFn: () => api.lifestyle.workouts.list({ limit: 15 }),
  });

  const create = useMutation({
    mutationFn: () => {
      const locationType = draft.locationType;
      if (!isActivityType(locationType)) {
        throw new Error('Pick where you trained');
      }
      return api.lifestyle.workouts.create({
        locationType: locationType as ActivityTypeId,
        durationMinutes: numberOrNull(draft.durationMinutes),
        intensityRating: numberOrNull(draft.intensityRating),
        rawText: draft.rawText.trim(),
        notes: draft.notes.trim(),
      });
    },
    onSuccess: () => {
      // Only clear the draft once the server has the session.
      clearWorkoutDraft();
      setDraft(EMPTY_DRAFT);
      setError(null);
      queryClient.invalidateQueries({ queryKey: ['lifestyle'] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const set = <K extends keyof WorkoutDraft>(key: K, value: WorkoutDraft[K]) =>
    setDraft(d => ({ ...d, [key]: value }));

  return (
    <section className="p-4 rounded-lg bg-[var(--color-surface)] border border-white/10 flex flex-col min-w-0">
      <div className="flex items-baseline justify-between gap-2 mb-3">
        <h2 className="text-lg font-semibold text-[var(--color-text)]">
          Workout log
        </h2>
        {restored && !isDraftEmpty(draft) && (
          <span className="text-xs text-[var(--color-text-muted)]">
            Draft restored
          </span>
        )}
      </div>

      <textarea
        value={draft.rawText}
        onChange={e => set('rawText', e.target.value)}
        placeholder={PLACEHOLDER}
        rows={5}
        className="w-full px-3 py-2 rounded bg-[var(--color-bg)] border border-white/10 text-[var(--color-text)] font-mono text-sm resize-y focus:outline-none focus:border-[var(--color-primary)]"
      />

      <div className="mt-3 flex flex-wrap gap-2">
        {ACTIVITY_TYPES.map(type => {
          const active = draft.locationType === type;
          return (
            <button
              key={type}
              type="button"
              onClick={() => set('locationType', active ? null : type)}
              aria-pressed={active}
              className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-full text-xs border transition-colors min-h-[36px] ${
                active
                  ? 'border-[var(--color-primary)] text-[var(--color-text)] bg-white/5'
                  : 'border-white/10 text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
              }`}
            >
              <span
                className="inline-block w-2.5 h-2.5 rounded-sm"
                style={{ background: ACTIVITY_COLORS[type] }}
                aria-hidden="true"
              />
              {ACTIVITY_LABELS[type]}
            </button>
          );
        })}
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2">
        <label className="text-xs text-[var(--color-text-muted)]">
          Duration (min)
          <input
            type="number"
            inputMode="numeric"
            min={0}
            value={draft.durationMinutes}
            onChange={e => set('durationMinutes', e.target.value)}
            className="mt-1 w-full px-3 py-2 rounded bg-[var(--color-bg)] border border-white/10 text-[var(--color-text)] focus:outline-none focus:border-[var(--color-primary)]"
          />
        </label>
        <label className="text-xs text-[var(--color-text-muted)]">
          Intensity (RPE 1–10)
          <input
            type="number"
            inputMode="numeric"
            min={1}
            max={10}
            value={draft.intensityRating}
            onChange={e => set('intensityRating', e.target.value)}
            className="mt-1 w-full px-3 py-2 rounded bg-[var(--color-bg)] border border-white/10 text-[var(--color-text)] focus:outline-none focus:border-[var(--color-primary)]"
          />
        </label>
      </div>

      <input
        value={draft.notes}
        onChange={e => set('notes', e.target.value)}
        placeholder="Notes (optional)"
        className="mt-2 w-full px-3 py-2 rounded bg-[var(--color-bg)] border border-white/10 text-[var(--color-text)] text-sm focus:outline-none focus:border-[var(--color-primary)]"
      />

      {error && (
        <div className="mt-2 text-xs text-[var(--color-accent)]">{error}</div>
      )}

      <button
        type="button"
        onClick={() => create.mutate()}
        disabled={create.isPending || isDraftEmpty(draft)}
        className="mt-3 px-4 py-2 rounded bg-[var(--color-primary)] text-white font-medium disabled:opacity-40 min-h-[44px]"
      >
        {create.isPending ? 'Saving…' : 'Log workout'}
      </button>

      {sessions.length > 0 && (
        <ul className="mt-4 space-y-2">
          {sessions.map(session => (
            <SessionCard key={session.id} session={session} />
          ))}
        </ul>
      )}
    </section>
  );
}
