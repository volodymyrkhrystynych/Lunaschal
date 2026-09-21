import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/hooks/api';
import type { ActivityTypeId, WorkoutSession } from '@/hooks/api';
import {
  ACTIVITY_COLORS,
  ACTIVITY_LABELS,
  ACTIVITY_TYPES,
  formatSets,
} from '@/lib/lifestyle';
import { IntensityPicker, IntensityStars } from './IntensityStars';
import { CARD, CARD_DIVIDER } from './card';
import {
  clearWorkoutDraft,
  DRAFT_SAVE_DELAY_MS,
  EMPTY_DRAFT,
  loadWorkoutDraft,
  saveWorkoutDraft,
} from '@/lib/workoutDraft';

const PLACEHOLDER = 'bicep curls 20, 10';

/** How many past sessions the card lists. */
const HISTORY_LIMIT = 4;

function numberOrNull(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const n = Number(trimmed);
  return Number.isFinite(n) ? n : null;
}

function SessionCard({
  session,
  showDelete,
}: {
  session: WorkoutSession;
  showDelete: boolean;
}) {
  const queryClient = useQueryClient();
  const [showRaw, setShowRaw] = useState(false);
  const [editing, setEditing] = useState(false);
  const [location, setLocation] = useState(session.locationType);
  const [rating, setRating] = useState(String(session.intensityRating ?? ''));
  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ['lifestyle'] });

  const reparse = useMutation({
    mutationFn: () => api.lifestyle.workouts.reparse(session.id),
    onSuccess: invalidate,
  });
  const update = useMutation({
    mutationFn: () =>
      api.lifestyle.workouts.update(session.id, {
        ...(location !== 'unassigned' ? { locationType: location } : {}),
        intensityRating: numberOrNull(rating),
      }),
    onSuccess: () => {
      setEditing(false);
      invalidate();
    },
  });
  const remove = useMutation({
    mutationFn: () => api.lifestyle.workouts.delete(session.id),
    onSuccess: invalidate,
  });

  const meta = [
    session.durationMinutes ? `${session.durationMinutes} min` : null,
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
          {session.intensityRating != null && (
            <IntensityStars value={session.intensityRating} />
          )}
        </div>
        <div className="flex items-center gap-2 text-xs">
          <button
            type="button"
            onClick={() => {
              setLocation(session.locationType);
              setRating(String(session.intensityRating ?? ''));
              setEditing(!editing);
            }}
          >
            {editing ? 'Cancel' : 'Rate / location'}
          </button>
          {session.rawText && (
            <button
              type="button"
              onClick={() => setShowRaw(v => !v)}
              className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            >
              {showRaw ? 'Hide raw' : 'Raw'}
            </button>
          )}
          {showDelete && (
            <button
              type="button"
              onClick={() => remove.mutate()}
              className="text-[var(--color-accent)] hover:opacity-80"
            >
              Delete
            </button>
          )}
        </div>
      </div>

      {session.startedAt && session.endedAt && (
        <p className="mt-1 text-xs text-[var(--color-text-muted)]">
          {new Date(session.startedAt).toLocaleTimeString([], {
            hour: 'numeric',
            minute: '2-digit',
          })}
          –
          {new Date(session.endedAt).toLocaleTimeString([], {
            hour: 'numeric',
            minute: '2-digit',
          })}
        </p>
      )}
      {editing && (
        <div className="mt-3 space-y-3">
          {session.captureKind !== 'outdoor' && (
            <label className="block text-xs">
              Location
              <select
                aria-label="Location"
                value={location}
                onChange={e => setLocation(e.target.value as ActivityTypeId)}
                className="ml-2 bg-[var(--color-bg)] border border-white/20 rounded p-2"
              >
                <option value="unassigned" disabled>
                  Choose location
                </option>
                {ACTIVITY_TYPES.map(t => (
                  <option key={t} value={t}>
                    {ACTIVITY_LABELS[t]}
                  </option>
                ))}
              </select>
            </label>
          )}
          <IntensityPicker value={rating} onChange={setRating} />
          <button
            type="button"
            disabled={update.isPending}
            onClick={() => update.mutate()}
            className="px-3 py-2 rounded bg-[var(--color-primary)] text-white"
          >
            Save details
          </button>
        </div>
      )}
      {(update.error || remove.error || reparse.error) && (
        <p role="alert">
          {(update.error || remove.error || reparse.error)?.message}
        </p>
      )}

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
                {formatSets(ex.sets)}
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

export function WorkoutLog() {
  const client = useQueryClient();
  const [text, setText] = useState(() => loadWorkoutDraft()?.rawText ?? '');
  const [restored] = useState(() => Boolean(loadWorkoutDraft()?.rawText));
  const [selected, setSelected] = useState<string | undefined>(
    () => loadWorkoutDraft()?.selectedExercise
  );
  const [showDelete, setShowDelete] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);
  const submitting = useRef(false);
  const { data: recent = [] } = useQuery({
    queryKey: ['lifestyle', 'recent-exercises'],
    queryFn: api.lifestyle.workouts.recentExercises,
  });
  const { data: sessions = [] } = useQuery({
    queryKey: ['lifestyle', 'workouts'],
    queryFn: () => api.lifestyle.workouts.list({ limit: HISTORY_LIMIT }),
    refetchInterval: 60000,
  });
  const pills = [...recent];
  for (const name of ['walking', 'cycling']) {
    if (!pills.some(p => p.name === name))
      pills.push({
        name,
        displayName: name === 'walking' ? 'Walking' : 'Cycling',
      });
  }
  const active = selected ?? recent[0]?.name;
  useEffect(() => {
    const save = () =>
      saveWorkoutDraft({
        ...EMPTY_DRAFT,
        rawText: text,
        selectedExercise: active,
      });
    const timer = setTimeout(save, DRAFT_SAVE_DELAY_MS);
    const hidden = () => {
      if (document.visibilityState === 'hidden') save();
    };
    window.addEventListener('pagehide', save);
    document.addEventListener('visibilitychange', hidden);
    return () => {
      clearTimeout(timer);
      window.removeEventListener('pagehide', save);
      document.removeEventListener('visibilitychange', hidden);
    };
  }, [text, active]);
  const create = useMutation({
    mutationFn: (entry: string) =>
      api.lifestyle.workouts.addEntry({ text: entry, exercise: active }),
    onSuccess: result => {
      clearWorkoutDraft();
      setText('');
      setSelected(result.exercise);
      client.setQueryData<{ name: string; displayName: string }[]>(
        ['lifestyle', 'recent-exercises'],
        old =>
          [
            {
              name: result.exercise,
              displayName:
                result.session.exercises.find(
                  e => e.nameCanonical === result.exercise
                )?.displayName ?? result.exercise,
            },
            ...(old ?? []).filter(e => e.name !== result.exercise),
          ].slice(0, 10)
      );
      client.invalidateQueries({ queryKey: ['lifestyle'] });
      input.current?.focus();
    },
    onSettled: () => {
      submitting.current = false;
    },
  });
  const submit = () => {
    if (!text.trim() || submitting.current) return;
    submitting.current = true;
    create.mutate(text.trim());
  };
  return (
    <section className={CARD}>
      <div className="flex justify-between items-baseline mb-3">
        <h2 className="text-lg font-semibold">Workout log</h2>
        {restored && text && <span className="text-xs">Draft restored</span>}
        {sessions.length > 0 && (
          <button
            type="button"
            title={showDelete ? 'Hide delete buttons' : 'Show delete buttons'}
            onClick={() => setShowDelete(!showDelete)}
          >
            🗑
          </button>
        )}
      </div>
      <div className="flex flex-col gap-3 max-w-xl">
        <div
          aria-label="Recent exercises"
          className="flex gap-2 overflow-x-auto pb-1"
        >
          {pills.map(p => (
            <button
              key={p.name}
              type="button"
              aria-pressed={active === p.name}
              onClick={() => {
                setSelected(p.name);
                input.current?.focus();
              }}
              className={`shrink-0 rounded-full border px-3 py-2 text-sm ${active === p.name ? 'border-[var(--color-primary)] bg-white/10' : 'border-white/20'}`}
            >
              {p.displayName}
            </button>
          ))}
        </div>
        <textarea
          ref={input}
          aria-label="Exercise entry"
          value={text}
          readOnly={create.isPending}
          onChange={e => setText(e.target.value)}
          rows={2}
          placeholder={PLACEHOLDER}
          onKeyDown={e => {
            if (
              e.key === 'Enter' &&
              !e.shiftKey &&
              !e.nativeEvent.isComposing
            ) {
              e.preventDefault();
              submit();
            }
          }}
          className="w-full rounded border border-white/20 bg-[var(--color-bg)] p-3 text-sm"
        />
        <p className="text-xs text-[var(--color-text-muted)]">
          {active
            ? `Selected: ${pills.find(p => p.name === active)?.displayName ?? active}. `
            : 'Name your first exercise. '}
          Weight in lb, reps: 20, 10 · Bodyweight: 10 · Walking / cycling:
          minutes. Enter to save.
        </p>
        {create.error && (
          <p role="alert" className="text-sm text-[var(--color-accent)]">
            {create.error.message}
          </p>
        )}
        <button
          type="button"
          disabled={create.isPending || !text.trim()}
          onClick={submit}
          className="self-start rounded bg-[var(--color-primary)] px-4 py-2 text-white disabled:opacity-40"
        >
          {create.isPending ? 'Saving…' : 'Log set / activity'}
        </button>
        <p className="text-xs text-[var(--color-text-muted)]">
          Sets join one workout until an hour passes without a set. Add
          intensity and location below afterward.
        </p>
      </div>
      {sessions.length > 0 && (
        <div className={CARD_DIVIDER}>
          <h3 className="text-xs uppercase mb-2">Recent</h3>
          <ul className="space-y-2 max-h-96 overflow-y-auto pr-1">
            {sessions.map(session => (
              <SessionCard
                key={session.id}
                session={session}
                showDelete={showDelete}
              />
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
