import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api, type DelegateProposalRecord } from '../../hooks/api';

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
    case 'task':
      return 'Added to tasks';
    case 'flashcards':
      return `Queued ${p.result?.count ?? 0} card${p.result?.count === 1 ? '' : 's'} for review in Learning`;
    default:
      return 'Resolved';
  }
}

function str(data: Record<string, unknown>, key: string): string {
  return typeof data[key] === 'string' ? (data[key] as string).trim() : '';
}

/** One pending card's headline + detail, matching the copy each of these
 * carried as its own fixed-position card before proposals moved onto the
 * message metadata. */
function ProposalBody({ p }: { p: DelegateProposalRecord }) {
  const data = p.data;
  switch (p.kind) {
    case 'calendar':
      return (
        <>
          <div className="text-sm font-medium text-[var(--color-text)]">
            Save as calendar event?
          </div>
          <div className="text-sm text-[var(--color-text-muted)] mt-1">
            <span className="font-medium">{str(data, 'title')}</span>
            {str(data, 'date') && (
              <span className="ml-2">({str(data, 'date')})</span>
            )}
          </div>
          {Array.isArray(data.tags) && data.tags.length > 0 && (
            <div className="flex gap-1 mt-2">
              {(data.tags as string[]).map(tag => (
                <span
                  key={tag}
                  className="px-2 py-0.5 text-xs bg-white/10 rounded text-[var(--color-text-muted)]"
                >
                  {tag}
                </span>
              ))}
            </div>
          )}
        </>
      );
    case 'calorie':
      return (
        <>
          <div className="text-sm font-medium text-[var(--color-text)]">
            Log calories?
          </div>
          <div className="text-sm text-[var(--color-text-muted)] mt-1">
            <span className="font-medium">{str(data, 'description')}</span>
            <span className="ml-2">({String(data.calories ?? '')} cal)</span>
          </div>
        </>
      );
    case 'task':
      return (
        <>
          <div className="text-sm font-medium text-[var(--color-text)]">
            Add to your tasks?
          </div>
          <div className="text-sm text-[var(--color-text-muted)] mt-1">
            <span className="font-medium">{str(data, 'title')}</span>
          </div>
        </>
      );
    case 'flashcards':
      return (
        <>
          <div className="text-sm font-medium text-[var(--color-text)]">
            Generate flashcards for "{str(data, 'topic')}"?
          </div>
          <div className="text-sm text-[var(--color-text-muted)] mt-1">
            I'll generate atomic cards and queue them for your approval in the
            Learning tab.
          </div>
        </>
      );
    default:
      return null;
  }
}

const ACCEPT_LABEL: Record<string, string> = {
  calendar: 'Save',
  calorie: 'Log',
  task: 'Add',
  flashcards: 'Queue Cards',
};

const ACCEPT_PENDING_LABEL: Record<string, string> = {
  calendar: 'Saving...',
  calorie: 'Logging...',
  task: 'Adding...',
  flashcards: 'Generating...',
};

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
    }: {
      proposalId: string;
      action: 'accept' | 'dismiss';
    }) => api.chat.resolveProposal(messageId, proposalId, action),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['chat', 'today'] });
      queryClient.invalidateQueries({ queryKey: ['calendar'] });
      queryClient.invalidateQueries({ queryKey: ['lifestyle', 'calories'] });
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

        const busy = busyId === p.id;
        return (
          <div
            key={p.id}
            className="rounded-lg border border-white/10 bg-[var(--color-surface)]/60 p-3"
          >
            <div className="flex items-start gap-3">
              <div className="flex-1">
                <ProposalBody p={p} />
              </div>
              <div className="flex gap-2 shrink-0">
                <button
                  onClick={() =>
                    resolve.mutate({ proposalId: p.id, action: 'dismiss' })
                  }
                  disabled={busy}
                  className="px-3 py-1 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
                >
                  Dismiss
                </button>
                <button
                  onClick={() =>
                    resolve.mutate({ proposalId: p.id, action: 'accept' })
                  }
                  disabled={busy}
                  className="px-3 py-1 text-sm bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
                >
                  {busy ? ACCEPT_PENDING_LABEL[p.kind] : ACCEPT_LABEL[p.kind]}
                </button>
              </div>
            </div>
            {resolve.isError && resolve.variables?.proposalId === p.id && (
              <div className="mt-2 text-xs text-red-400">
                {resolve.error instanceof Error
                  ? resolve.error.message
                  : 'Could not save that — try again.'}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
