import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';

/**
 * What the assistant has noted about the user for itself, and the delete button
 * that makes those notes fair to write.
 *
 * The chat delegate's `remember` tool writes here instantly, with no
 * confirmation card — the same trade `create_note_to_self` and `add_todos`
 * make. That trade only holds while this list exists: an immediate write the
 * user cannot see is one they cannot undo, and the previous version of this
 * tool wrote the user's own document instead, which is why it was removed.
 *
 * Deliberately no editor. A note the user wants to keep but reword belongs in
 * the document above, in their words; keeping a half-corrected assistant note
 * around would just be a second memory saying something slightly different.
 */
export function ObservationsPanel() {
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: ['memory', 'observations'],
    queryFn: api.memory.observations,
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.memory.deleteObservation(id),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ['memory', 'observations'] }),
  });

  const observations = data?.observations ?? [];
  const maxPending = data?.maxPending ?? 40;

  return (
    <div className="space-y-2 pt-3 border-t border-white/10">
      <div>
        <h4 className="text-sm text-[var(--color-text)]">
          What the assistant has noticed
        </h4>
        <p className="text-xs text-[var(--color-text-muted)] mt-1">
          Notes it wrote itself during conversations, carried in every prompt
          alongside the document above. It cannot edit them once written and it
          stops adding at {maxPending}. Delete anything wrong or no longer true.
        </p>
      </div>

      {observations.length === 0 ? (
        <p className="text-xs text-[var(--color-text-muted)]">
          Nothing noted yet.
        </p>
      ) : (
        <>
          <ul className="space-y-1">
            {observations.map(observation => (
              <li
                key={observation.id}
                className="flex items-start justify-between gap-2 rounded border border-white/10 p-2 text-xs"
              >
                <div className="min-w-0">
                  <p className="text-[var(--color-text)] break-words">
                    {observation.content}
                  </p>
                  <p className="text-[var(--color-text-muted)] mt-0.5">
                    {new Date(observation.createdAt).toLocaleString()}
                  </p>
                </div>
                <button
                  type="button"
                  aria-label={`Delete note: ${observation.content}`}
                  onClick={() => remove.mutate(observation.id)}
                  disabled={remove.isPending}
                  className="shrink-0 px-2 py-0.5 rounded bg-white/10 text-[var(--color-text)] hover:bg-white/15 disabled:opacity-50"
                >
                  Delete
                </button>
              </li>
            ))}
          </ul>
          <p className="text-xs text-[var(--color-text-muted)]">
            {observations.length} / {maxPending}
          </p>
        </>
      )}
    </div>
  );
}
