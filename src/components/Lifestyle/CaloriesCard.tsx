import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/hooks/api';
import { parseCalorieEntry } from '@/lib/lifestyle';
import { ulid } from '@/lib/ulid';
import { useCalorieLog } from '@/offline/mutationDefaults';
import { CARD } from './card';

/**
 * A light manual calorie log — a description, a number, and today's running
 * total. No macros and no food database (docs/lifestyle-tab.md §6); the richer
 * photo/review food log is its own Food tab.
 *
 * Entry is one box: "chicken breast and rice, ~600" splits into description and
 * kcal locally. That's plain string parsing rather than an LLM round trip — the
 * format is regular, and a calorie note shouldn't wait on a model.
 */
export function CaloriesCard() {
  const queryClient = useQueryClient();
  const [text, setText] = useState('');
  const [error, setError] = useState<string | null>(null);

  const { data: day } = useQuery({
    queryKey: ['lifestyle', 'calories'],
    queryFn: () => api.lifestyle.calories.day(),
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ['lifestyle', 'calories'] });

  // Queued, and optimistic: the line appears in today's list and the running
  // total moves the moment it is typed, whether or not the backend is there.
  const create = useCalorieLog({
    onError: (e: Error) => setError(e.message),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.lifestyle.calories.delete(id),
    onSuccess: invalidate,
  });

  const submit = () => {
    const parsed = parseCalorieEntry(text);
    if (!parsed) {
      setError(
        'End the line with a calorie count, e.g. "rice and chicken 600"'
      );
      return;
    }
    create.mutate({ id: ulid(), ...parsed });
    // Cleared here rather than on success: the entry is already in the list
    // (optimistically) and may not reach the server for hours.
    setText('');
    setError(null);
  };

  const preview = parseCalorieEntry(text);
  const entries = day?.entries ?? [];

  return (
    <section className={CARD}>
      <div className="flex items-baseline justify-between gap-2 mb-3">
        <h2 className="text-lg font-semibold text-[var(--color-text)]">
          Calories
        </h2>
        <span className="text-sm text-[var(--color-text-muted)]">
          Today:{' '}
          <span className="text-[var(--color-text)] font-medium tabular-nums">
            {day?.total ?? 0}
          </span>{' '}
          kcal
        </span>
      </div>

      <div className="flex gap-2">
        <input
          value={text}
          onChange={e => {
            setText(e.target.value);
            setError(null);
          }}
          onKeyDown={e => {
            if (e.key === 'Enter') submit();
          }}
          placeholder="chicken breast and rice, ~600"
          className="flex-1 min-w-0 px-3 py-2 rounded bg-[var(--color-bg)] border border-white/10 text-[var(--color-text)] text-sm focus:outline-none focus:border-[var(--color-primary)]"
        />
        <button
          type="button"
          onClick={submit}
          disabled={create.isPending || !text.trim()}
          className="px-3 py-2 rounded bg-[var(--color-primary)] text-white text-sm disabled:opacity-40 min-h-[44px]"
        >
          Add
        </button>
      </div>

      {preview && (
        <div className="mt-1 text-xs text-[var(--color-text-muted)]">
          {preview.description} — {preview.calories} kcal
        </div>
      )}
      {error && (
        <div className="mt-1 text-xs text-[var(--color-accent)]">{error}</div>
      )}

      {entries.length === 0 ? (
        <div className="mt-3 text-sm text-[var(--color-text-muted)]">
          Nothing logged today.
        </div>
      ) : (
        <ul className="mt-3 space-y-1">
          {entries.map(entry => (
            <li
              key={entry.id}
              className="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-white/5 group"
            >
              <span className="flex-1 min-w-0 text-sm text-[var(--color-text)] truncate">
                {entry.description}
              </span>
              <span className="text-sm text-[var(--color-text-muted)] tabular-nums">
                {entry.calories}
              </span>
              <button
                type="button"
                onClick={() => remove.mutate(entry.id)}
                aria-label={`Delete ${entry.description}`}
                className="opacity-0 group-hover:opacity-100 focus:opacity-100 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
