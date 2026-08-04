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

  const answer = useMutation({
    mutationFn: ({ id, text }: { id: string; text: string }) =>
      api.ideas.answerQuestion(id, { answer: text }),
    onSuccess: invalidate,
  });

  const impl = resolveImplementation({
    userVerdict,
    verdict: assessment?.verdict ?? null,
    confidence: assessment?.confidence ?? null,
    assessmentStale: assessment?.stale ?? false,
  });
  const open = (questions ?? []).filter(q => q.status === 'open');
  const answered = (questions ?? []).filter(q => q.status === 'answered');

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

      {open.length > 0 && (
        <div className="mb-2">
          <h4 className="text-xs font-medium text-[var(--color-text)] mb-1">
            Needs a decision ({open.length})
          </h4>
          <ul className="space-y-2">
            {open.map(question => (
              <li key={question.id}>
                <p className="text-xs text-[var(--color-text)]">
                  {question.question}
                </p>
                {question.why && (
                  <p className="text-xs text-[var(--color-text-muted)]">
                    {question.why}
                  </p>
                )}
                <input
                  defaultValue=""
                  placeholder={
                    question.options.length
                      ? question.options.join(' / ')
                      : 'Your answer…'
                  }
                  aria-label={`Answer: ${question.question}`}
                  onBlur={e => {
                    const text = e.target.value.trim();
                    if (text) answer.mutate({ id: question.id, text });
                  }}
                  className="w-full mt-1 bg-transparent text-xs text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border-b border-white/10 focus:outline-none focus:border-[var(--color-primary)]"
                />
              </li>
            ))}
          </ul>
        </div>
      )}

      {answered.length > 0 && (
        <details className="text-xs text-[var(--color-text-muted)]">
          <summary className="cursor-pointer">
            {answered.length} decision{answered.length === 1 ? '' : 's'} made
          </summary>
          <ul className="mt-1 space-y-0.5">
            {answered.map(q => (
              <li key={q.id}>
                · {q.question} →{' '}
                <span className="text-[var(--color-text)]">{q.answer}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
