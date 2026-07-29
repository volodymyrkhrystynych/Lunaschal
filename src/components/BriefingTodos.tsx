import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  api,
  BriefingTodoDecision,
  ProposedTodo,
  TodoList,
} from '../hooks/api';
import { dueInputToUnix, formatCompletedAt } from '../lib/todos';

interface Props {
  messageId: string;
  proposals: ProposedTodo[];
}

const PRIORITIES = [1, 2, 3, 4, 5];
const LISTS: TodoList[] = ['todo', 'chores', 'archive'];

// Unix seconds -> local 'YYYY-MM-DD' for an <input type="date">.
function dueUnixToInput(due: number | null): string {
  if (!due) return '';
  const d = new Date(due * 1000);
  if (isNaN(d.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

// `duplicate` only appears on briefings written before linking existed.
const STATUS_LABEL: Record<string, string> = {
  done: 'Done',
  accepted: 'Added to to-dos',
  rejected: 'Dismissed',
  duplicate: 'Already on your list — skipped',
};

// Shares the Tasks view's timestamp format so a completion reads the same
// wherever you see it. Legacy items have no resolvedAt — show no time at all.
function resolvedTime(p: ProposedTodo): string {
  if (!p.resolvedAt) return '';
  return formatCompletedAt(new Date(p.resolvedAt * 1000).toISOString());
}

const linkLabel = (p: ProposedTodo) =>
  p.linkedType === 'daily' ? 'daily task' : 'on your to-dos';

// Edits held while a card is still pending. Keyed by proposal id; absent means
// "unchanged", so the server keeps whatever the briefing proposed.
type Draft = { title: string; priority: number; due: string; list: TodoList };

/** The briefing's plan for the day, shown under the briefing message. Items are
 *  crossed off here rather than added to the to-do list — that keeps the backlog
 *  clean. An item that restates something already on the to-dos or daily tasks
 *  carries a link, and crossing it off completes that row too. */
export function BriefingTodos({ messageId, proposals }: Props) {
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const queryClient = useQueryClient();

  const decide = useMutation({
    mutationFn: (decisions: BriefingTodoDecision[]) =>
      api.chat.decideBriefingTodos(messageId, decisions),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['chat', 'today'] });
      queryClient.invalidateQueries({ queryKey: ['todos'] });
      // A linked daily task completes in the Tasks view too.
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
    },
  });

  const draftFor = (p: ProposedTodo): Draft =>
    drafts[p.id] ?? {
      title: p.title,
      priority: p.priority,
      due: dueUnixToInput(p.due),
      list: p.list,
    };

  const setDraft = (p: ProposedTodo, patch: Partial<Draft>) =>
    setDrafts(d => ({ ...d, [p.id]: { ...draftFor(p), ...patch } }));

  const acceptDecision = (p: ProposedTodo): BriefingTodoDecision => {
    const d = draftFor(p);
    return {
      id: p.id,
      action: 'accept',
      title: d.title,
      priority: d.priority,
      due: dueInputToUnix(d.due),
      list: d.list,
    };
  };

  const pending = proposals.filter(p => p.status === 'pending');
  // A linked item is already on a list — there's nothing to add.
  const addable = pending.filter(p => !p.linkedId);
  const busy = decide.isPending;

  return (
    <div className="mt-2 rounded-lg border border-white/10 bg-[var(--color-surface)]/60 p-3">
      <div className="flex items-center justify-between gap-2 mb-2">
        <div className="text-xs font-medium text-[var(--color-text-muted)]">
          Today's plan
          {pending.length > 0 && ` — ${pending.length} to go`}
        </div>
        {pending.length > 1 && (
          <div className="flex gap-2">
            <button
              onClick={() =>
                decide.mutate(
                  pending.map(p => ({ id: p.id, action: 'reject' as const }))
                )
              }
              disabled={busy}
              className="px-2 py-0.5 text-xs rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
            >
              Dismiss all
            </button>
            {addable.length > 1 && (
              <button
                onClick={() => decide.mutate(addable.map(acceptDecision))}
                disabled={busy}
                className="px-2 py-0.5 text-xs rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
              >
                Add all to to-dos
              </button>
            )}
          </div>
        )}
      </div>

      <div className="space-y-2">
        {proposals.map(p => {
          if (p.status !== 'pending') {
            const time = resolvedTime(p);
            return (
              <div
                key={p.id}
                className="flex items-baseline justify-between gap-2 rounded border border-white/5 px-2 py-1.5 opacity-60"
              >
                <span
                  className={`text-sm ${p.status === 'rejected' ? 'text-[var(--color-text-muted)] line-through' : 'text-[var(--color-text)]'}`}
                >
                  {p.title}
                </span>
                <span className="text-xs text-[var(--color-text-muted)] shrink-0">
                  {STATUS_LABEL[p.status] ?? p.status}
                  {time && ` · ${time}`}
                </span>
              </div>
            );
          }

          const d = draftFor(p);
          const linked = Boolean(p.linkedId);
          return (
            <div
              key={p.id}
              className="rounded border border-white/10 bg-white/5 px-2 py-2"
            >
              <div className="flex items-baseline gap-2">
                {/* A linked item's title is fixed — editing it would break the
                    tie to the real row it crosses off. */}
                {linked ? (
                  <span className="flex-1 text-sm text-[var(--color-text)] py-0.5">
                    {p.title}
                  </span>
                ) : (
                  <input
                    value={d.title}
                    onChange={e => setDraft(p, { title: e.target.value })}
                    disabled={busy}
                    aria-label="To-do title"
                    className="flex-1 min-w-0 bg-transparent text-sm text-[var(--color-text)] border-b border-transparent hover:border-white/10 focus:border-[var(--color-primary)] focus:outline-none py-0.5"
                  />
                )}
                {linked && (
                  <span
                    title={
                      p.linkedTitle && p.linkedTitle !== p.title
                        ? `Linked to "${p.linkedTitle}"`
                        : undefined
                    }
                    className="shrink-0 text-[10px] uppercase tracking-wide text-[var(--color-text-muted)] border border-white/10 rounded px-1.5 py-0.5"
                  >
                    {linkLabel(p)}
                  </span>
                )}
              </div>

              <div className="mt-2 flex flex-wrap items-center gap-2">
                {!linked && (
                  <>
                    <select
                      value={d.list}
                      onChange={e =>
                        setDraft(p, { list: e.target.value as TodoList })
                      }
                      disabled={busy}
                      aria-label="List"
                      className="bg-[var(--color-surface)] border border-white/10 rounded px-1.5 py-0.5 text-xs text-[var(--color-text)]"
                    >
                      {LISTS.map(l => (
                        <option key={l} value={l}>
                          {l}
                        </option>
                      ))}
                    </select>
                    <select
                      value={d.priority}
                      onChange={e =>
                        setDraft(p, { priority: Number(e.target.value) })
                      }
                      disabled={busy}
                      aria-label="Priority"
                      className="bg-[var(--color-surface)] border border-white/10 rounded px-1.5 py-0.5 text-xs text-[var(--color-text)]"
                    >
                      {PRIORITIES.map(n => (
                        <option key={n} value={n}>
                          P{n}
                        </option>
                      ))}
                    </select>
                    <input
                      type="date"
                      value={d.due}
                      onChange={e => setDraft(p, { due: e.target.value })}
                      disabled={busy}
                      aria-label="Due date"
                      className="bg-[var(--color-surface)] border border-white/10 rounded px-1.5 py-0.5 text-xs text-[var(--color-text)]"
                    />
                  </>
                )}
                <div className="ml-auto flex items-center gap-2">
                  <button
                    onClick={() =>
                      decide.mutate([{ id: p.id, action: 'reject' }])
                    }
                    disabled={busy}
                    className="px-2 py-0.5 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
                  >
                    Dismiss
                  </button>
                  {!linked && (
                    <button
                      onClick={() => decide.mutate([acceptDecision(p)])}
                      disabled={busy || !d.title.trim()}
                      className="px-2 py-0.5 text-xs rounded border border-white/10 text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
                    >
                      Add to to-dos
                    </button>
                  )}
                  <button
                    onClick={() =>
                      decide.mutate([{ id: p.id, action: 'done' }])
                    }
                    disabled={busy}
                    className="px-2 py-0.5 text-xs rounded bg-[var(--color-primary)] text-white hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
                  >
                    Done
                  </button>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {decide.isError && (
        <div className="mt-2 text-xs text-red-400">
          Could not save that decision — try again.
        </div>
      )}
    </div>
  );
}
