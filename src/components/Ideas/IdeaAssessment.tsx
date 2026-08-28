import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type IdeaVerdict } from '../../hooks/api';
import {
  EFFORT_LABELS,
  implementationClasses,
  implementationLabel,
  resolveImplementation,
} from '../../lib/ideas';

interface IdeaAssessmentProps {
  ideaId: string;
  userVerdict: IdeaVerdict | null;
}

/**
 * The agent's "is this already built?" call, always shown with the evidence it
 * cited and always overridable. A verdict you cannot check or correct is the
 * version of this feature that quietly starts lying.
 */
export function IdeaAssessment({ ideaId, userVerdict }: IdeaAssessmentProps) {
  const queryClient = useQueryClient();
  const [assessment, setAssessment] = useState<Awaited<
    ReturnType<typeof api.ideas.assess>
  > | null>(null);
  const [error, setError] = useState('');

  const { data: questions } = useQuery({
    queryKey: ['ideas', ideaId, 'questions'],
    queryFn: () => api.ideas.listQuestions(ideaId),
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['ideas'] });
    queryClient.invalidateQueries({ queryKey: ['ideas', ideaId, 'questions'] });
  };

  const assess = useMutation({
    mutationFn: () => api.ideas.assess(ideaId),
    onSuccess: result => {
      setAssessment(result);
      setError('');
      invalidate();
    },
    onError: e =>
      setError(e instanceof Error ? e.message : 'Assessment failed'),
  });

  const override = useMutation({
    mutationFn: (verdict: IdeaVerdict | null) =>
      api.ideas.update(ideaId, { userVerdict: verdict }),
    onSuccess: invalidate,
  });

  const impl = resolveImplementation({
    userVerdict,
    verdict: assessment?.verdict ?? null,
    confidence: assessment?.confidence ?? null,
    assessmentStale: assessment?.stale ?? false,
  });
  // Answering them belongs to IdeaDecisions, directly below; this is only the
  // pointer that a "Check the repo" run turned some up.
  const open = (questions ?? []).filter(q => q.status === 'open').length;

  return (
    <div className="mx-4 my-4 border-t border-white/10 pt-4">
      <div className="flex items-center gap-2 mb-2">
        <h3 className="text-sm font-medium text-[var(--color-text)]">
          Already built?
        </h3>
        <span
          className={`px-1.5 py-0.5 rounded text-xs ${implementationClasses(impl)}`}
        >
          {implementationLabel(impl)}
        </span>
        <button
          type="button"
          onClick={() => assess.mutate()}
          disabled={assess.isPending}
          className="px-2 py-0.5 rounded text-xs bg-white/10 text-[var(--color-text)] hover:bg-white/15 disabled:opacity-50"
        >
          {assess.isPending ? 'Checking…' : 'Check the repo'}
        </button>
        {assessment?.effort && (
          <span className="text-xs text-[var(--color-text-muted)]">
            Effort: {EFFORT_LABELS[assessment.effort]}
          </span>
        )}
      </div>

      {error && <p className="text-xs text-red-400">{error}</p>}

      {assessment?.rationale && (
        <p className="text-xs text-[var(--color-text-muted)] mb-2">
          {assessment.rationale}
        </p>
      )}

      {assessment?.evidence?.length ? (
        <ul className="text-xs text-[var(--color-text-muted)] mb-2 space-y-0.5">
          {assessment.evidence.map(item => (
            <li key={`${item.kind}:${item.ref}`}>
              · <span className="text-[var(--color-text)]">{item.ref}</span>{' '}
              <code>
                {item.file}
                {item.line ? `:${item.line}` : ''}
              </code>
            </li>
          ))}
        </ul>
      ) : null}

      {assessment?.onRoadmap?.length ? (
        <p className="text-xs text-[var(--color-text-muted)] mb-2">
          On the roadmap (planned, not built): {assessment.onRoadmap.join('; ')}
        </p>
      ) : null}

      <div className="flex items-center gap-1 text-xs mb-3">
        <span className="text-[var(--color-text-muted)]">Your call:</span>
        {(['no', 'partial', 'yes'] as IdeaVerdict[]).map(v => (
          <button
            key={v}
            type="button"
            onClick={() => override.mutate(userVerdict === v ? null : v)}
            className={`px-1.5 py-0.5 rounded ${
              userVerdict === v
                ? 'bg-[var(--color-primary)]/20 text-[var(--color-primary)]'
                : 'bg-white/5 text-[var(--color-text-muted)] hover:bg-white/10'
            }`}
          >
            {v === 'no' ? 'Not built' : v === 'partial' ? 'Partly' : 'Built'}
          </button>
        ))}
      </div>

      {open > 0 && (
        <p className="text-xs text-amber-300">
          {open} open decision{open === 1 ? '' : 's'} below.
        </p>
      )}
    </div>
  );
}
