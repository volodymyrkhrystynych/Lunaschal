import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { api } from '@/hooks/api';
import type { FilledAnswer } from '@/hooks/api';
import { answerSummary, parseQuestionList } from '@/lib/jobs';
import { SteerBar } from './SteerBar';

const SOURCE_LABELS: Record<FilledAnswer['source'], string> = {
  profile: 'from your profile',
  bank: 'saved answer',
  generated: 'written for this job',
  unanswered: 'needs you',
};

const SOURCE_STYLES: Record<FilledAnswer['source'], string> = {
  profile: 'text-emerald-300 border-emerald-500/30',
  bank: 'text-sky-300 border-sky-500/30',
  generated: 'text-[var(--color-primary)] border-[var(--color-primary)]/30',
  unanswered: 'text-amber-300 border-amber-500/30',
};

function AnswerCard({ answer }: { answer: FilledAnswer }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(answer.answer);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard is unavailable on an insecure origin; the text is on screen
      // and selectable either way, so this is not worth an error state.
    }
  };

  return (
    <div className="rounded-lg border border-white/10 bg-[var(--color-surface)] p-3">
      <div className="flex items-start justify-between gap-2 mb-1">
        <p className="text-sm font-medium text-[var(--color-text)]">
          {answer.label}
        </p>
        <span
          className={`shrink-0 text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded border ${SOURCE_STYLES[answer.source]}`}
        >
          {SOURCE_LABELS[answer.source]}
        </span>
      </div>

      {answer.answer ? (
        <p className="text-sm text-[var(--color-text)] whitespace-pre-wrap mb-2">
          {answer.answer}
        </p>
      ) : (
        <p className="text-sm text-[var(--color-text-muted)] italic mb-2">
          No answer — write this one yourself.
        </p>
      )}

      <button
        type="button"
        onClick={copy}
        disabled={!answer.answer}
        className="min-h-[36px] px-3 rounded text-xs border border-white/20 bg-white/5 hover:bg-white/10 disabled:opacity-40"
      >
        {copied ? 'Copied ✓' : 'Copy'}
      </button>
    </div>
  );
}

/**
 * The always-works path: paste the form's questions, get a stack of
 * tap-to-copy answers plus the tailored resume to attach.
 *
 * No page injection, no proxy, nothing that a site redesign can break — which
 * is why this ships before the browser overlay rather than after it. On a
 * phone it is tap, switch app, paste, switch back.
 */
export function AnswerKit({
  applicationId,
  steer,
  onSteerChange,
}: {
  applicationId: string;
  steer: string;
  onSteerChange: (steer: string) => void;
}) {
  const [raw, setRaw] = useState('');
  const [answers, setAnswers] = useState<FilledAnswer[] | null>(null);

  const fill = useMutation({
    mutationFn: (nextSteer: string) => {
      const questions = parseQuestionList(raw);
      if (questions.length === 0) {
        return Promise.reject(new Error('Paste the form’s questions first.'));
      }
      return api.jobs.applications.answers(applicationId, questions, nextSteer);
    },
    onSuccess: result => setAnswers(result.answers),
  });

  const summary = answers ? answerSummary(answers) : null;

  return (
    <div className="space-y-3">
      <div>
        <label className="block text-xs text-[var(--color-text-muted)] mb-1">
          The form’s questions, one per line
        </label>
        <textarea
          value={raw}
          onChange={e => setRaw(e.target.value)}
          rows={4}
          placeholder={
            'Why do you want to work here?\nYears of Python experience?\nSalary expectation?'
          }
          className="w-full p-2 rounded bg-[var(--color-bg)] border border-white/10 text-sm text-[var(--color-text)] resize-y"
        />
      </div>

      <SteerBar
        steer={steer}
        onSteerChange={onSteerChange}
        onRun={nextSteer => fill.mutate(nextSteer)}
        busy={fill.isPending}
        runLabel="Fill answers"
      />

      {fill.isError && (
        <p className="text-sm text-red-400">{(fill.error as Error).message}</p>
      )}

      {summary && (
        <p className="text-xs text-[var(--color-text-muted)]">
          {summary.ready} of {summary.total} answered
          {summary.free > 0 && ` · ${summary.free} straight from your profile`}
          {summary.unanswered > 0 && ` · ${summary.unanswered} need you`}
        </p>
      )}

      <div className="space-y-2">
        {answers?.map((answer, i) => (
          <AnswerCard key={`${answer.label}-${i}`} answer={answer} />
        ))}
      </div>
    </div>
  );
}
