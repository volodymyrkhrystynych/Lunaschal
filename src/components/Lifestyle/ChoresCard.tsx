import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/hooks/api';
import { groupTodosByList, partitionTodos } from '@/lib/todos';
import { useTodoUpdate } from '@/offline/mutationDefaults';
import { TodoForm } from '../Tasks/TodoForm';
import { TodoRow } from '../Tasks/TodoRow';

/**
 * Chores, promoted out of Tasks into a first-class section here.
 *
 * These are the *same rows* the Tasks view shows — todos with list='chores' —
 * not a parallel table. Ticking one off here and ticking it off there are the
 * same action, and the completion still lands in the Journal feed via the
 * existing task_events log.
 *
 * So this card renders them through the Tasks view's own `TodoRow`/`TodoForm`
 * rather than a lookalike list: same markup, same due/repeat/priority editing,
 * and — the reason it matters — the same **visibility rule**. `partitionTodos`
 * hides a repeating chore until it comes within ~10% of its interval of being
 * due (`isFarOffPeriodic`), so a monthly chore doesn't sit here nagging for
 * four weeks. Duplicating the row markup here once meant duplicating that rule,
 * which meant not having it.
 */
export function ChoresCard() {
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);

  const { data: todos = [], isLoading } = useQuery({
    queryKey: ['todos'],
    queryFn: api.todos.list,
  });
  const { active } = useMemo(
    () => partitionTodos(groupTodosByList(todos).chores),
    [todos]
  );

  // Offline-queueable; the optimistic update and invalidation live in the
  // registered mutation defaults, shared with the Tasks view.
  const updateTodo = useTodoUpdate();
  const deleteTodo = useMutation({
    mutationFn: (id: string) => api.todos.remove(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['todos'] });
      // Deleting an active chore logs a "removed" notification in the Journal.
      queryClient.invalidateQueries({ queryKey: ['taskEvents'] });
    },
  });

  return (
    <section className="p-4 rounded-lg bg-[var(--color-surface)] border border-white/10 flex flex-col min-w-0">
      <h2 className="text-lg font-semibold text-[var(--color-text)] mb-3">
        Chores
      </h2>

      {creating ? (
        <TodoForm list="chores" onCancel={() => setCreating(false)} />
      ) : (
        <button
          type="button"
          onClick={() => setCreating(true)}
          className="w-full mb-3 px-3 py-2 rounded-lg border border-dashed border-white/15 text-left text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-white/30 transition-colors"
        >
          + Add chore…
        </button>
      )}

      {isLoading && (
        <div className="text-[var(--color-text-muted)] text-sm">Loading…</div>
      )}

      <div className="space-y-2">
        {active.map(todo => (
          <TodoRow
            key={todo.id}
            todo={todo}
            selected={false}
            ringed={false}
            // Keyboard list navigation belongs to the Tasks view, which owns
            // the shortcut scopes; here a row is just a row.
            onSelect={() => {}}
            onUpdate={data => updateTodo.mutate({ id: todo.id, data })}
            onDelete={() => deleteTodo.mutate(todo.id)}
          />
        ))}

        {active.length === 0 && !isLoading && (
          <div className="text-center py-6 text-[var(--color-text-muted)] text-sm">
            Nothing due.
          </div>
        )}
      </div>
    </section>
  );
}
