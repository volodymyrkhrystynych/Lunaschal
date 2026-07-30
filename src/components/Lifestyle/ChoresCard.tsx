import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/hooks/api';

/**
 * Chores, promoted out of Tasks into a first-class section here.
 *
 * These are the *same rows* the Tasks view shows — todos with list='chores' —
 * not a parallel table. Ticking one off here and ticking it off there are the
 * same action, and the completion still lands in the Journal feed via the
 * existing task_events log.
 */
export function ChoresCard() {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState('');

  const { data: todos = [], isLoading } = useQuery({
    queryKey: ['todos'],
    queryFn: api.todos.list,
  });
  const chores = todos.filter(t => t.list === 'chores');

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ['todos'] });

  const create = useMutation({
    mutationFn: (name: string) =>
      api.todos.create({ title: name, list: 'chores' }),
    onSuccess: () => {
      setTitle('');
      invalidate();
    },
  });
  const toggle = useMutation({
    mutationFn: ({ id, done }: { id: string; done: boolean }) =>
      api.todos.update(id, { done }),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.todos.remove(id),
    onSuccess: invalidate,
  });

  const add = () => {
    const name = title.trim();
    if (name) create.mutate(name);
  };

  return (
    <section className="p-4 rounded-lg bg-[var(--color-surface)] border border-white/10 flex flex-col min-w-0">
      <h2 className="text-lg font-semibold text-[var(--color-text)] mb-3">
        Chores
      </h2>

      <div className="flex gap-2">
        <input
          value={title}
          onChange={e => setTitle(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter') add();
          }}
          placeholder="Add a chore"
          className="flex-1 min-w-0 px-3 py-2 rounded bg-[var(--color-bg)] border border-white/10 text-[var(--color-text)] text-sm focus:outline-none focus:border-[var(--color-primary)]"
        />
        <button
          type="button"
          onClick={add}
          disabled={!title.trim() || create.isPending}
          className="px-3 py-2 rounded bg-[var(--color-primary)] text-white text-sm disabled:opacity-40 min-h-[44px]"
        >
          Add
        </button>
      </div>

      {isLoading ? (
        <div className="mt-3 text-sm text-[var(--color-text-muted)]">
          Loading…
        </div>
      ) : chores.length === 0 ? (
        <div className="mt-3 text-sm text-[var(--color-text-muted)]">
          No chores yet.
        </div>
      ) : (
        <ul className="mt-3 space-y-1">
          {chores.map(chore => (
            <li
              key={chore.id}
              className="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-white/5 group"
            >
              <input
                type="checkbox"
                checked={chore.done}
                onChange={() =>
                  toggle.mutate({ id: chore.id, done: !chore.done })
                }
                className="w-4 h-4 shrink-0 accent-[var(--color-primary)]"
              />
              <span
                className={`flex-1 min-w-0 text-sm truncate ${
                  chore.done
                    ? 'line-through text-[var(--color-text-muted)]'
                    : 'text-[var(--color-text)]'
                }`}
              >
                {chore.title}
              </span>
              <button
                type="button"
                onClick={() => remove.mutate(chore.id)}
                aria-label={`Delete ${chore.title}`}
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
