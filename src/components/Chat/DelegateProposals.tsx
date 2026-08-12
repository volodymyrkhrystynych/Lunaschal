import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api, type DelegateProposalRecord } from '../../hooks/api';
import { PRIORITY_LABELS } from '../../lib/todos';

interface Props {
  messageId: string;
  proposals: DelegateProposalRecord[];
}

// Same idea as BriefingTodos.tsx's STATUS_LABEL: once a card is resolved it
// collapses to one quiet line rather than disappearing, so the transcript
// keeps a record of what actually happened.
function resolvedLabel(p: DelegateProposalRecord): string {
  if (p.status === 'dismissed') return 'Dismissed';
  switch (p.kind) {
    case 'calendar':
      return 'Saved to calendar';
    case 'calorie':
      return 'Logged calories';
    case 'food':
      return p.result?.calorieLogId
        ? 'Saved to your food log, with calories'
        : 'Saved to your food log';
    case 'task':
      return 'Added to tasks';
    case 'recipe':
      return 'Saved to your recipes';
    case 'recipe_link':
      return 'Linked to your recipe';
    case 'flashcards':
      return `Queued ${p.result?.count ?? 0} card${p.result?.count === 1 ? '' : 's'} for review in Learning`;
    default:
      return 'Resolved';
  }
}

const HEADLINE: Record<string, string> = {
  calendar: 'Save as calendar event?',
  calorie: 'Log calories?',
  food: 'Add to your food log?',
  task: 'Add to your tasks?',
  recipe: 'Save this recipe?',
  recipe_link: 'Link this meal to that recipe?',
  flashcards: 'Generate flashcards?',
};

const ACCEPT_LABEL: Record<string, string> = {
  calendar: 'Save',
  calorie: 'Log',
  food: 'Log Meal',
  task: 'Add',
  recipe: 'Save',
  recipe_link: 'Link',
  flashcards: 'Queue Cards',
};

const ACCEPT_PENDING_LABEL: Record<string, string> = {
  calendar: 'Saving...',
  calorie: 'Logging...',
  food: 'Logging...',
  task: 'Adding...',
  recipe: 'Saving...',
  recipe_link: 'Linking...',
  flashcards: 'Generating...',
};

// Matches TodoForm.tsx — the card is the same kind of small entry form, and a
// second visual language for the same job in the same app reads as a bug.
const fieldClass =
  'bg-[var(--color-surface)] border border-white/20 rounded px-2 py-1 text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)] outline-none focus:border-[var(--color-primary)]';

function str(data: Record<string, unknown>, key: string): string {
  const v = data[key];
  return typeof v === 'string' ? v : typeof v === 'number' ? String(v) : '';
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex items-center gap-2">
      <span className="text-xs text-[var(--color-text-muted)] w-16 shrink-0">
        {label}
      </span>
      {children}
    </label>
  );
}

/** The editable body of one pending card, per kind.
 *
 * Editable because the card was read-only and the values on it are a model's
 * reading of a sentence: a due date one day out, or a priority it guessed,
 * could only be fixed by dismissing the card and going to the Tasks view to
 * type the whole thing again. `data` is the live edit state; accepting posts
 * it, and the server re-validates through the same handler either way.
 */
function ProposalForm({
  kind,
  data,
  set,
}: {
  kind: string;
  data: Record<string, unknown>;
  set: (patch: Record<string, unknown>) => void;
}) {
  switch (kind) {
    case 'task':
      return (
        <div className="space-y-1.5">
          <input
            aria-label="Title"
            value={str(data, 'title')}
            onChange={e => set({ title: e.target.value })}
            className={`${fieldClass} w-full`}
          />
          <input
            aria-label="Notes"
            value={str(data, 'notes')}
            onChange={e => set({ notes: e.target.value })}
            placeholder="More information…"
            className={`${fieldClass} w-full text-xs`}
          />
          <div className="flex flex-wrap gap-x-4 gap-y-1.5">
            <Field label="Due">
              <input
                type="date"
                value={str(data, 'due')}
                onChange={e => set({ due: e.target.value || null })}
                className={fieldClass}
              />
            </Field>
            <Field label="Priority">
              <select
                value={typeof data.priority === 'number' ? data.priority : 3}
                onChange={e => set({ priority: Number(e.target.value) })}
                className={fieldClass}
              >
                {Object.entries(PRIORITY_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {value} — {label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="List">
              <select
                value={str(data, 'list') || 'todo'}
                onChange={e => set({ list: e.target.value })}
                className={fieldClass}
              >
                <option value="todo">To-do</option>
                <option value="chores">Chores</option>
                <option value="archive">Archive</option>
              </select>
            </Field>
            <Field label="Every">
              <input
                aria-label="Repeat interval"
                type="number"
                min={1}
                value={str(data, 'repeatInterval')}
                onChange={e =>
                  set({
                    repeatInterval: e.target.value
                      ? Number(e.target.value)
                      : null,
                  })
                }
                placeholder="—"
                className={`${fieldClass} w-16`}
              />
              <select
                aria-label="Repeat unit"
                value={str(data, 'repeatUnit') || 'week'}
                onChange={e => set({ repeatUnit: e.target.value })}
                className={fieldClass}
              >
                <option value="day">days</option>
                <option value="week">weeks</option>
                <option value="month">months</option>
              </select>
            </Field>
          </div>
        </div>
      );

    case 'calendar': {
      const allDay = data.allDay === true;
      return (
        <div className="space-y-1.5">
          <input
            aria-label="Title"
            value={str(data, 'title')}
            onChange={e => set({ title: e.target.value })}
            className={`${fieldClass} w-full`}
          />
          <input
            aria-label="Description"
            value={str(data, 'description')}
            onChange={e => set({ description: e.target.value })}
            placeholder="More information…"
            className={`${fieldClass} w-full text-xs`}
          />
          <div className="flex flex-wrap gap-x-4 gap-y-1.5">
            <Field label="Date">
              <input
                type="date"
                value={str(data, 'date')}
                onChange={e => set({ date: e.target.value })}
                className={fieldClass}
              />
            </Field>
            {/* An all-day event is explicitly the whole day, not merely
                untimed, so ticking it clears the clocks rather than leaving
                them to be silently ignored on save. */}
            <label className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
              <input
                type="checkbox"
                checked={allDay}
                onChange={e =>
                  set(
                    e.target.checked
                      ? { allDay: true, time: null, endTime: null }
                      : { allDay: false }
                  )
                }
              />
              All day
            </label>
            {!allDay && (
              <>
                <Field label="From">
                  <input
                    type="time"
                    value={str(data, 'time')}
                    onChange={e => set({ time: e.target.value || null })}
                    className={fieldClass}
                  />
                </Field>
                <Field label="To">
                  <input
                    type="time"
                    value={str(data, 'endTime')}
                    onChange={e => set({ endTime: e.target.value || null })}
                    className={fieldClass}
                  />
                </Field>
              </>
            )}
          </div>
          <Field label="Tags">
            <input
              value={
                Array.isArray(data.tags)
                  ? (data.tags as string[]).join(', ')
                  : ''
              }
              onChange={e =>
                set({
                  tags: e.target.value
                    .split(',')
                    .map(t => t.trim())
                    .filter(Boolean),
                })
              }
              placeholder="comma, separated"
              className={`${fieldClass} flex-1 text-xs`}
            />
          </Field>
        </div>
      );
    }

    case 'calorie':
      return (
        <div className="flex flex-wrap gap-x-4 gap-y-1.5 items-center">
          <input
            aria-label="Description"
            value={str(data, 'description')}
            onChange={e => set({ description: e.target.value })}
            className={`${fieldClass} flex-1 min-w-40`}
          />
          <Field label="Calories">
            <input
              type="number"
              min={0}
              value={str(data, 'calories')}
              onChange={e =>
                set({
                  calories: e.target.value ? Number(e.target.value) : null,
                })
              }
              className={`${fieldClass} w-24`}
            />
          </Field>
        </div>
      );

    case 'food':
      return (
        <div className="space-y-1.5">
          <input
            aria-label="Dish"
            value={str(data, 'dish')}
            onChange={e => set({ dish: e.target.value })}
            className={`${fieldClass} w-full`}
          />
          <div className="flex flex-wrap gap-x-4 gap-y-1.5 items-center">
            <Field label="Place">
              <input
                value={str(data, 'place')}
                onChange={e => set({ place: e.target.value })}
                className={`${fieldClass} w-36`}
              />
            </Field>
            <Field label="Calories">
              <input
                type="number"
                min={0}
                value={str(data, 'calories')}
                onChange={e =>
                  set({
                    calories: e.target.value ? Number(e.target.value) : null,
                  })
                }
                placeholder="—"
                className={`${fieldClass} w-24`}
              />
            </Field>
            <Field label="Rating">
              <input
                type="number"
                min={1}
                max={5}
                value={str(data, 'rating')}
                onChange={e =>
                  set({
                    rating: e.target.value ? Number(e.target.value) : null,
                  })
                }
                placeholder="—"
                className={`${fieldClass} w-16`}
              />
            </Field>
          </div>
          <textarea
            aria-label="Notes"
            value={str(data, 'notes')}
            onChange={e => set({ notes: e.target.value })}
            rows={2}
            placeholder="What you said about it"
            className={`${fieldClass} w-full resize-none`}
          />
          {/* Said plainly because neither is an editable field here: both come
              off the message itself when the card is accepted, so an edit can't
              rewrite what was actually said. */}
          <div className="text-xs text-[var(--color-text-muted)]">
            Your photo and exactly what you said are saved with it.
          </div>
        </div>
      );

    case 'recipe':
      return (
        <div className="space-y-1.5">
          <input
            aria-label="Title"
            value={str(data, 'title')}
            onChange={e => set({ title: e.target.value })}
            className={`${fieldClass} w-full`}
          />
          <textarea
            aria-label="Content"
            value={str(data, 'content')}
            onChange={e => set({ content: e.target.value })}
            rows={10}
            className={`${fieldClass} w-full resize-none`}
          />
          <Field label="Tags">
            <input
              value={
                Array.isArray(data.tags)
                  ? (data.tags as string[]).join(', ')
                  : ''
              }
              onChange={e =>
                set({
                  tags: e.target.value
                    .split(',')
                    .map(t => t.trim())
                    .filter(Boolean),
                })
              }
              placeholder="comma, separated"
              className={`${fieldClass} flex-1 text-xs`}
            />
          </Field>
        </div>
      );

    case 'recipe_link':
      // Not editable: entryId/recipeId are the whole point of this card, and
      // rewriting either would point the link at a different row than what
      // was actually offered.
      return (
        <div className="text-sm text-[var(--color-text)]">
          <span className="font-medium">{str(data, 'dish')}</span>
          {' → '}
          <span className="font-medium">{str(data, 'recipeTitle')}</span>
        </div>
      );

    case 'flashcards':
      return (
        <div className="space-y-1.5">
          <input
            aria-label="Topic"
            value={str(data, 'topic')}
            onChange={e => set({ topic: e.target.value })}
            className={`${fieldClass} w-full`}
          />
          <div className="text-xs text-[var(--color-text-muted)]">
            I'll generate atomic cards and queue them for your approval in the
            Learning tab.
          </div>
        </div>
      );

    default:
      return null;
  }
}

function PendingCard({
  proposal,
  busy,
  error,
  onResolve,
}: {
  proposal: DelegateProposalRecord;
  busy: boolean;
  error: string | null;
  onResolve: (
    action: 'accept' | 'dismiss',
    data: Record<string, unknown>
  ) => void;
}) {
  // Seeded once from what was staged; the row on the server is not touched
  // until accept, so an abandoned edit costs nothing.
  const [data, setData] = useState<Record<string, unknown>>(proposal.data);
  const set = (patch: Record<string, unknown>) =>
    setData(d => ({ ...d, ...patch }));

  return (
    <div className="rounded-lg border border-white/10 bg-[var(--color-surface)]/60 p-3">
      <div className="text-sm font-medium text-[var(--color-text)] mb-2">
        {HEADLINE[proposal.kind] ?? 'Save this?'}
      </div>
      <ProposalForm kind={proposal.kind} data={data} set={set} />
      <div className="flex gap-2 justify-end mt-2">
        <button
          onClick={() => onResolve('dismiss', data)}
          disabled={busy}
          className="px-3 py-1 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
        >
          Dismiss
        </button>
        <button
          onClick={() => onResolve('accept', data)}
          disabled={busy}
          className="px-3 py-1 text-sm bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
        >
          {busy
            ? ACCEPT_PENDING_LABEL[proposal.kind]
            : ACCEPT_LABEL[proposal.kind]}
        </button>
      </div>
      {error && <div className="mt-2 text-xs text-red-400">{error}</div>}
    </div>
  );
}

/** Delegate confirm cards for one assistant message, read straight from its
 * persisted metadata (backend/delegate/runs.py stamps each with an id and
 * 'pending' status when the run finishes) — durable across a reload or a
 * dropped connection, and resolved in place by POST
 * /api/chat/proposals/<messageId>/<id> so a card leaves "pending" only when
 * the user actually accepts or dismisses it. */
export function DelegateProposals({ messageId, proposals }: Props) {
  const queryClient = useQueryClient();

  const resolve = useMutation({
    mutationFn: ({
      proposalId,
      action,
      data,
    }: {
      proposalId: string;
      action: 'accept' | 'dismiss';
      data?: Record<string, unknown>;
    }) => api.chat.resolveProposal(messageId, proposalId, action, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['chat', 'today'] });
      queryClient.invalidateQueries({ queryKey: ['calendar'] });
      queryClient.invalidateQueries({ queryKey: ['lifestyle', 'calories'] });
      queryClient.invalidateQueries({ queryKey: ['food'] });
      queryClient.invalidateQueries({ queryKey: ['cookbook'] });
      queryClient.invalidateQueries({ queryKey: ['todos'] });
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
      queryClient.invalidateQueries({ queryKey: ['learning'] });
    },
  });

  const busyId =
    resolve.isPending && resolve.variables
      ? resolve.variables.proposalId
      : null;

  return (
    <div className="mt-2 space-y-2">
      {proposals.map(p => {
        if (p.status !== 'pending') {
          return (
            <div
              key={p.id}
              className="flex items-baseline justify-between gap-2 rounded border border-white/5 px-2 py-1.5 opacity-60"
            >
              <span className="text-sm text-[var(--color-text)]">
                {resolvedLabel(p)}
              </span>
            </div>
          );
        }

        const failed =
          resolve.isError && resolve.variables?.proposalId === p.id
            ? resolve.error instanceof Error
              ? resolve.error.message
              : 'Could not save that — try again.'
            : null;

        return (
          <PendingCard
            key={p.id}
            proposal={p}
            busy={busyId === p.id}
            error={failed}
            onResolve={(action, data) =>
              resolve.mutate(
                action === 'accept'
                  ? { proposalId: p.id, action, data }
                  : { proposalId: p.id, action }
              )
            }
          />
        );
      })}
    </div>
  );
}
