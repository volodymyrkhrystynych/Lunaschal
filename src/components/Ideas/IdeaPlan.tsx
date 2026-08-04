import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { MessageMarkdown } from '../MessageMarkdown';

interface IdeaPlanProps {
  ideaId: string;
}

/**
 * The plan is a document you hand to a separate coding agent, so the only
 * actions that matter are "make one" and "copy it".
 */
export function IdeaPlan({ ideaId }: IdeaPlanProps) {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState('');
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState('');

  const { data: versions } = useQuery({
    queryKey: ['ideas', ideaId, 'plans'],
    queryFn: () => api.ideas.listPlans(ideaId),
  });

  const planId = selectedId || versions?.[0]?.id || '';
  const { data: plan } = useQuery({
    queryKey: ['ideas', 'plan', planId],
    queryFn: () => api.ideas.getPlan(planId),
    enabled: !!planId,
  });

  const create = useMutation({
    mutationFn: () => api.ideas.createPlan(ideaId),
    onSuccess: created => {
      setSelectedId(created.id);
      setError('');
      queryClient.invalidateQueries({ queryKey: ['ideas', ideaId, 'plans'] });
      queryClient.invalidateQueries({ queryKey: ['ideas'] });
    },
    onError: e =>
      setError(
        e instanceof Error ? e.message : 'The plan could not be generated'
      ),
  });

  const copy = async () => {
    if (!plan) return;
    await navigator.clipboard.writeText(plan.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="mx-4 my-4 border-t border-white/10 pt-4">
      <div className="flex items-center gap-2 mb-2">
        <h3 className="text-sm font-medium text-[var(--color-text)]">Plan</h3>
        <button
          type="button"
          onClick={() => create.mutate()}
          disabled={create.isPending}
          className="px-2 py-0.5 rounded text-xs bg-white/10 text-[var(--color-text)] hover:bg-white/15 disabled:opacity-50"
        >
          {create.isPending
            ? 'Writing…'
            : versions?.length
              ? 'Regenerate'
              : 'Create plan'}
        </button>
        {versions && versions.length > 1 && (
          <select
            value={planId}
            onChange={e => setSelectedId(e.target.value)}
            aria-label="Plan version"
            className="rounded bg-[var(--color-surface)] border border-white/10 px-1 py-0.5 text-xs text-[var(--color-text)] focus:outline-none"
          >
            {versions.map(v => (
              <option key={v.id} value={v.id}>
                v{v.version}
              </option>
            ))}
          </select>
        )}
        <span className="flex-1" />
        {plan && (
          <button
            type="button"
            onClick={copy}
            className="px-2 py-0.5 rounded text-xs bg-white/10 text-[var(--color-text)] hover:bg-white/15"
          >
            {copied ? 'Copied' : 'Copy markdown'}
          </button>
        )}
      </div>

      {create.isPending && (
        <p className="text-xs text-[var(--color-text-muted)]">
          Writing the spec — this takes a minute on a local model.
        </p>
      )}
      {error && <p className="text-xs text-red-400">{error}</p>}

      {!versions?.length && !create.isPending && !error && (
        <p className="text-xs text-[var(--color-text-muted)]">
          No plan yet. Generating one writes high-level specs plus technical
          considerations, ready to hand to a coding agent.
        </p>
      )}

      {plan && (
        <div className="mt-2 text-sm text-[var(--color-text)] max-h-[60vh] overflow-y-auto rounded bg-[var(--color-bg)] border border-white/10 p-3">
          <MessageMarkdown content={plan.content} />
        </div>
      )}
    </div>
  );
}
