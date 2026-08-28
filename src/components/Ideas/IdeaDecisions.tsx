import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type IdeaQuestion } from '../../hooks/api';
import { useRecorder } from '../../hooks/useRecorder';
import {
  decisionAnswer,
  decisionChoices,
  selectedChoice,
  OTHER_CHOICE,
} from '../../lib/ideas';

interface IdeaDecisionsProps {
  ideaId: string;
}

/**
 * The forks the agent found, answered the way a plan-mode question is answered:
 * pick one of the options it proposed, or take the last row and write your own.
 *
 * It used to be a bare text input with the options crammed into the
 * placeholder — which asked the user to retype an answer that was already on
 * screen, and gave the model's suggestions no more standing than grey hint
 * text. The write-your-own row is always last and always present: the agent
 * proposes the forks it can see, and the answer it didn't think of has to be
 * reachable without leaving the list.
 */
export function IdeaDecisions({ ideaId }: IdeaDecisionsProps) {
  const queryClient = useQueryClient();

  const { data: questions } = useQuery({
    queryKey: ['ideas', ideaId, 'questions'],
    queryFn: () => api.ideas.listQuestions(ideaId),
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['ideas'] });
    queryClient.invalidateQueries({ queryKey: ['ideas', ideaId, 'questions'] });
  };

  const answer = useMutation({
    mutationFn: ({ id, text }: { id: string; text: string }) =>
      api.ideas.answerQuestion(id, { answer: text }),
    onSuccess: invalidate,
  });

  const dismiss = useMutation({
    mutationFn: (id: string) =>
      api.ideas.answerQuestion(id, { status: 'dismissed' }),
    onSuccess: invalidate,
  });

  const open = (questions ?? []).filter(q => q.status === 'open');
  const answered = (questions ?? []).filter(q => q.status === 'answered');

  return (
    <div className="mx-4 my-4 border-t border-white/10 pt-4">
      <h3 className="text-sm font-medium text-[var(--color-text)] mb-2">
        Decisions
      </h3>

      {open.length === 0 && answered.length === 0 && (
        <p className="text-xs text-[var(--color-text-muted)]">
          Nothing to decide yet. Checking the repo above is what turns up the
          forks — the places where two reasonable answers lead to different
          work.
        </p>
      )}

      {open.length > 0 && (
        <ul className="space-y-4">
          {open.map(question => (
            <li key={question.id}>
              <DecisionCard
                question={question}
                pending={answer.isPending}
                onAnswer={text => answer.mutate({ id: question.id, text })}
                onDismiss={() => dismiss.mutate(question.id)}
              />
            </li>
          ))}
        </ul>
      )}

      {answered.length > 0 && (
        <details className="mt-3 text-xs text-[var(--color-text-muted)]">
          <summary className="cursor-pointer">
            {answered.length} decision{answered.length === 1 ? '' : 's'} made
          </summary>
          <ul className="mt-2 space-y-2">
            {answered.map(q => (
              <li key={q.id}>
                <DecisionCard
                  question={q}
                  pending={answer.isPending}
                  onAnswer={text => answer.mutate({ id: q.id, text })}
                  onDismiss={() => dismiss.mutate(q.id)}
                />
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

interface DecisionCardProps {
  question: IdeaQuestion;
  pending: boolean;
  onAnswer: (text: string) => void;
  onDismiss: () => void;
}

function DecisionCard({
  question,
  pending,
  onAnswer,
  onDismiss,
}: DecisionCardProps) {
  const settled = selectedChoice(question.options, question.answer);
  const [choice, setChoice] = useState(settled.value);
  const [note, setNote] = useState(settled.note);

  // Dictation lands in the box rather than submitting: a decision is committed
  // once and read by the planner afterwards, so a misheard word is worth the
  // chance to fix (the IdeaCapture/BrainDump pattern, not the chat one).
  const recorder = useRecorder(text =>
    setNote(prev => (prev ? `${prev} ${text}`.trim() : text))
  );
  const recording = recorder.status === 'recording';

  const choices = decisionChoices(question.options);
  const text = decisionAnswer(choice, note);
  const answered = question.status === 'answered';
  const unchanged = answered && text === (question.answer ?? '').trim();
  const name = `decision-${question.id}`;

  return (
    <div className="rounded border border-white/10 bg-[var(--color-bg)] p-3">
      <p className="text-sm text-[var(--color-text)]">{question.question}</p>
      {question.why && (
        <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">
          {question.why}
        </p>
      )}

      {/* aria-label rather than a <legend>: the question is already on screen
          directly above, and a visually-hidden legend just duplicates it. */}
      <fieldset className="mt-2" aria-label={question.question}>
        <div className="space-y-1">
          {choices.map(row => (
            <label
              key={row.value}
              className={`flex items-start gap-2 rounded px-2 py-1.5 text-xs cursor-pointer ${
                choice === row.value
                  ? 'bg-[var(--color-primary)]/15 text-[var(--color-text)]'
                  : 'text-[var(--color-text-muted)] hover:bg-white/5'
              }`}
            >
              <input
                type="radio"
                name={name}
                value={row.value}
                checked={choice === row.value}
                onChange={() => setChoice(row.value)}
                className="mt-0.5 accent-[var(--color-primary)]"
              />
              <span>{row.label}</span>
            </label>
          ))}
        </div>
      </fieldset>

      {choice === OTHER_CHOICE && (
        <div className="mt-2">
          <div className="flex gap-2">
            <textarea
              autoFocus
              value={note}
              onChange={e => setNote(e.target.value)}
              rows={2}
              placeholder="Your answer…"
              aria-label={`Answer: ${question.question}`}
              className="flex-1 resize-none rounded bg-[var(--color-surface)] border border-white/10 px-2 py-1.5 text-xs text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)]"
            />
            <button
              type="button"
              onClick={() => (recording ? recorder.stop() : recorder.start())}
              disabled={
                recorder.status === 'transcribing' || !recorder.canTranscribe
              }
              title={
                recorder.canTranscribe
                  ? undefined
                  : 'Offline — dictation needs the server'
              }
              aria-label={recording ? 'Stop recording' : 'Dictate your answer'}
              className={`self-end px-2 py-1.5 rounded text-xs ${
                recording
                  ? 'bg-red-500/25 text-red-300'
                  : 'bg-white/10 text-[var(--color-text)] hover:bg-white/15'
              } disabled:opacity-50`}
            >
              {recording ? '■' : recorder.status === 'transcribing' ? '…' : '●'}
            </button>
          </div>
          {recorder.error && (
            <p className="mt-1 text-xs text-red-400">{recorder.error}</p>
          )}
        </div>
      )}

      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          onClick={() => onAnswer(text)}
          disabled={!text || pending || unchanged}
          className="px-2 py-0.5 rounded text-xs bg-[var(--color-primary)]/20 text-[var(--color-primary)] hover:bg-[var(--color-primary)]/30 disabled:opacity-40"
        >
          {answered ? 'Update' : 'Decide'}
        </button>
        {!answered && (
          <button
            type="button"
            onClick={onDismiss}
            className="px-2 py-0.5 rounded text-xs text-[var(--color-text-muted)] hover:bg-white/10"
          >
            Not a decision
          </button>
        )}
        {answered && (
          <span className="text-xs text-emerald-300">
            Decided: {question.answer}
          </span>
        )}
      </div>
    </div>
  );
}
