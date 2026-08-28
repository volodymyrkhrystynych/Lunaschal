import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/hooks/api';
import type {
  ApplicationStatus,
  RecordedAnswer,
  ResumeVersion,
  TailoredContent,
} from '@/hooks/api';
import {
  coveragePercent,
  daysUntilPurge,
  PIPELINE_ORDER,
  rewrittenBullets,
  STATUS_LABELS,
} from '@/lib/jobs';
import { MasterDetailBack } from '../MasterDetailBack';
import { AnswerKit } from './AnswerKit';
import { SteerBar } from './SteerBar';

function KeywordBlock({ content }: { content: TailoredContent }) {
  const coverage = coveragePercent(content);
  const { matched, missing } = content.keywords;

  return (
    <div className="rounded-lg border border-white/10 bg-[var(--color-surface)] p-3">
      <p className="text-sm font-medium text-[var(--color-text)] mb-2">
        Keyword match{coverage !== null && ` · ${coverage}%`}
      </p>
      {matched.length > 0 && (
        <div className="mb-2">
          <p className="text-xs text-[var(--color-text-muted)] mb-1">
            Backed by your profile — mirrored in the resume
          </p>
          <div className="flex flex-wrap gap-1">
            {matched.map(term => (
              <span
                key={term}
                className="text-xs px-1.5 py-0.5 rounded border border-emerald-500/30 text-emerald-300"
              >
                {term}
              </span>
            ))}
          </div>
        </div>
      )}
      {missing.length > 0 && (
        <div>
          <p className="text-xs text-[var(--color-text-muted)] mb-1">
            Asked for, not in your profile — deliberately not claimed
          </p>
          <div className="flex flex-wrap gap-1">
            {missing.map(term => (
              <span
                key={term}
                className="text-xs px-1.5 py-0.5 rounded border border-amber-500/30 text-amber-300"
              >
                {term}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function RewriteReview({ content }: { content: TailoredContent }) {
  const changed = rewrittenBullets(content);
  if (changed.length === 0) return null;

  return (
    <details className="rounded-lg border border-white/10 bg-[var(--color-surface)] p-3">
      <summary className="text-sm font-medium text-[var(--color-text)] cursor-pointer">
        {changed.length} bullet{changed.length === 1 ? '' : 's'} reworded —
        check before sending
      </summary>
      <div className="mt-2 space-y-3">
        {changed.map(bullet => (
          <div key={bullet.bulletId} className="text-sm">
            <p className="text-[var(--color-text-muted)] line-through">
              {bullet.original}
            </p>
            <p className="text-[var(--color-text)]">{bullet.text}</p>
          </div>
        ))}
      </div>
    </details>
  );
}

function AtsReview({ review }: { review: ResumeVersion['review'] }) {
  if (!review?.metrics) return null;
  const percent = (value: number) => `${Math.round(value * 100)}%`;
  return (
    <div className="rounded-lg border border-white/10 bg-[var(--color-surface)] p-3 space-y-2">
      <p className="text-sm font-medium">Rendered ATS review</p>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
        <span>
          PDF text:{' '}
          {review.pdfChecked
            ? review.parseable
              ? 'healthy'
              : 'check'
            : 'not checked'}
        </span>
        <span>
          Reading order:{' '}
          {review.readingOrder === null
            ? '—'
            : review.readingOrder
              ? 'sane'
              : 'check'}
        </span>
        <span>Action verbs: {percent(review.metrics.actionVerbDensity)}</span>
        <span>
          Quantified impact: {percent(review.metrics.quantifiedImpactDensity)}
        </span>
      </div>
      {review.issues.length > 0 && (
        <ul className="text-xs text-amber-300 list-disc pl-5">
          {review.issues.map(issue => (
            <li key={issue}>{issue}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * Fix the wording by hand before sending.
 *
 * The server takes only the text: company, role and the original wording come
 * from the stored version, so an edit can reword an accomplishment but never
 * re-attribute it. Clearing a bullet's box removes it from the resume, which
 * is the only way to drop one.
 */
function ResumeEditor({
  version,
  content,
  disabled,
  onSaved,
}: {
  version: ResumeVersion;
  content: TailoredContent;
  disabled: boolean;
  onSaved: () => void;
}) {
  const [summary, setSummary] = useState(content.summary);
  const [texts, setTexts] = useState<Record<string, string>>({});

  // A re-tailor replaces the content under us; the draft has to follow or the
  // next save would write the previous generation's text back.
  // Keyed on the version id, deliberately not on `content`. React Query hands
  // back a fresh object on every refetch — including the one it does when the
  // window regains focus — so depending on the object identity would wipe
  // whatever the user was midway through typing the moment they alt-tabbed
  // back. A re-tailor produces a *new* version, which is the case that really
  // does need the draft replaced.
  useEffect(() => {
    setSummary(content.summary);
    setTexts(
      Object.fromEntries(content.selectedBullets.map(b => [b.bulletId, b.text]))
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version.id]);

  const save = useMutation({
    mutationFn: () =>
      api.jobs.resumes.edit(version.id, {
        summary,
        bullets: content.selectedBullets
          .map(b => ({
            bulletId: b.bulletId,
            text: texts[b.bulletId] ?? b.text,
          }))
          .filter(b => b.text.trim()),
      }),
    onSuccess: onSaved,
  });

  const dropped = content.selectedBullets.filter(
    b => !(texts[b.bulletId] ?? b.text).trim()
  ).length;

  return (
    <details className="rounded-lg border border-white/10 bg-[var(--color-surface)] p-3">
      <summary className="text-sm font-medium text-[var(--color-text)] cursor-pointer">
        Fix the wording
      </summary>

      <div className="mt-3 space-y-3">
        {content.draftReview && content.draftReview.length > 0 && (
          <div className="rounded border border-white/10 bg-white/5 p-2">
            <p className="text-xs font-medium">Reviewer notes applied</p>
            <ul className="text-xs text-[var(--color-text-muted)] list-disc pl-5 mt-1">
              {content.draftReview.map(note => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          </div>
        )}
        <label className="block">
          <span className="block text-xs text-[var(--color-text-muted)] mb-1">
            Summary
          </span>
          <textarea
            value={summary}
            rows={3}
            onChange={e => setSummary(e.target.value)}
            className="w-full p-2 rounded bg-[var(--color-bg)] border border-white/10 text-sm text-[var(--color-text)]"
          />
        </label>

        {content.selectedBullets.map(bullet => (
          <label key={bullet.bulletId} className="block">
            <span className="block text-xs text-[var(--color-text-muted)] mb-1">
              {bullet.company} · {bullet.roleTitle}
              {bullet.rewritten && ' · reworded by the model'}
            </span>
            <textarea
              value={texts[bullet.bulletId] ?? bullet.text}
              rows={2}
              onChange={e =>
                setTexts(prev => ({
                  ...prev,
                  [bullet.bulletId]: e.target.value,
                }))
              }
              className="w-full p-2 rounded bg-[var(--color-bg)] border border-white/10 text-sm text-[var(--color-text)]"
            />
            {bullet.rewritten && (
              <span className="block text-xs text-[var(--color-text-muted)] mt-0.5">
                Originally: {bullet.original}
              </span>
            )}
          </label>
        ))}

        {dropped > 0 && (
          <p className="text-xs text-amber-300">
            {dropped} empty bullet{dropped === 1 ? '' : 's'} will be removed
            from the resume.
          </p>
        )}
        {save.isError && (
          <p className="text-sm text-red-400">
            {(save.error as Error).message}
          </p>
        )}

        <button
          type="button"
          onClick={() => save.mutate()}
          disabled={disabled || save.isPending}
          className="min-h-[44px] px-4 rounded text-sm border border-white/20 bg-white/5 hover:bg-white/10 disabled:opacity-50"
        >
          {save.isPending ? 'Re-rendering…' : 'Save and re-render'}
        </button>
        {disabled && (
          <p className="text-xs text-[var(--color-text-muted)]">
            This application has been sent, so its resume is now the record of
            what the employer received. Re-tailor for a new version.
          </p>
        )}
      </div>
    </details>
  );
}

/** What was actually typed into the employer's form, captured by the extension. */
function RecordedAnswers({
  applicationId,
  answers,
  onChanged,
}: {
  applicationId: string;
  answers: RecordedAnswer[];
  onChanged: () => void;
}) {
  const remove = useMutation({
    mutationFn: (answerId: string) =>
      api.jobs.applications.recordedAnswers.remove(applicationId, answerId),
    onSuccess: onChanged,
  });

  if (answers.length === 0) {
    return (
      <p className="text-sm text-[var(--color-text-muted)]">
        Nothing recorded yet. The browser extension saves each answer as it
        fills the form, so this fills in while you apply.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      <p className="text-xs text-[var(--color-text-muted)]">
        Kept indefinitely — unlike the rendered files, which retention deletes.
      </p>
      {answers.map(answer => (
        <div
          key={answer.id}
          className="rounded-lg border border-white/10 bg-[var(--color-surface)] p-3"
        >
          <div className="flex items-start justify-between gap-2">
            <p className="text-xs text-[var(--color-text-muted)]">
              {answer.question}
            </p>
            <button
              type="button"
              onClick={() => remove.mutate(answer.id)}
              className="shrink-0 text-xs text-[var(--color-text-muted)] hover:text-red-400"
            >
              ×
            </button>
          </div>
          <p className="text-sm text-[var(--color-text)] whitespace-pre-wrap">
            {answer.answer}
          </p>
        </div>
      ))}
    </div>
  );
}

function NoteDraft({
  applicationId,
  status,
}: {
  applicationId: string;
  status: ApplicationStatus;
}) {
  const [context, setContext] = useState('');
  const [draft, setDraft] = useState<{ subject: string; body: string } | null>(
    null
  );
  const generate = useMutation({
    mutationFn: (kind: 'follow_up' | 'thank_you') =>
      api.jobs.applications.draftNote(applicationId, kind, context),
    onSuccess: setDraft,
  });
  const canThank = status === 'interview' || status === 'offer';

  return (
    <div className="rounded-lg border border-white/10 bg-[var(--color-surface)] p-3 space-y-2">
      <p className="text-sm font-medium text-[var(--color-text)]">
        Correspondence draft
      </p>
      <textarea
        value={context}
        onChange={e => setContext(e.target.value)}
        rows={2}
        placeholder="Optional: who you met and what you discussed"
        className="w-full p-2 rounded bg-[var(--color-bg)] border border-white/10 text-sm text-[var(--color-text)]"
      />
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => generate.mutate('follow_up')}
          disabled={generate.isPending}
          className="min-h-[44px] px-3 rounded text-sm border border-white/20 bg-white/5 disabled:opacity-50"
        >
          Draft follow-up
        </button>
        {canThank && (
          <button
            type="button"
            onClick={() => generate.mutate('thank_you')}
            disabled={generate.isPending}
            className="min-h-[44px] px-3 rounded text-sm border border-white/20 bg-white/5 disabled:opacity-50"
          >
            Draft thank-you
          </button>
        )}
      </div>
      {generate.isError && (
        <p className="text-xs text-red-400">
          {(generate.error as Error).message}
        </p>
      )}
      {draft && (
        <div className="space-y-1">
          <input
            value={draft.subject}
            onChange={e => setDraft({ ...draft, subject: e.target.value })}
            aria-label="Draft subject"
            className="w-full p-2 rounded bg-[var(--color-bg)] border border-white/10 text-sm"
          />
          <textarea
            value={draft.body}
            onChange={e => setDraft({ ...draft, body: e.target.value })}
            aria-label="Draft body"
            rows={6}
            className="w-full p-2 rounded bg-[var(--color-bg)] border border-white/10 text-sm"
          />
          <p className="text-xs text-[var(--color-text-muted)]">
            Review and copy into your mail client; nothing is sent
            automatically.
          </p>
        </div>
      )}
    </div>
  );
}

function InterviewPrep({ applicationId }: { applicationId: string }) {
  const queryClient = useQueryClient();
  const [notes, setNotes] = useState('');
  const [interviewer, setInterviewer] = useState('');
  const { data } = useQuery({
    queryKey: ['jobs', 'interview-prep', applicationId],
    queryFn: () => api.jobs.applications.interviewPrep.get(applicationId),
  });
  const generate = useMutation({
    mutationFn: () =>
      api.jobs.applications.interviewPrep.generate(applicationId, notes),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: ['jobs', 'interview-prep', applicationId],
      }),
  });
  const { data: researchData } = useQuery({
    queryKey: ['jobs', 'application-research', applicationId],
    queryFn: () => api.jobs.applications.research.get(applicationId),
  });
  const research = useMutation({
    mutationFn: () =>
      api.jobs.applications.research.generate(applicationId, interviewer),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: ['jobs', 'application-research', applicationId],
      }),
  });
  const pack = generate.data ?? data?.pack;
  const findings = research.data ?? researchData?.research;
  return (
    <div className="space-y-3">
      <textarea
        value={notes}
        onChange={e => setNotes(e.target.value)}
        rows={3}
        placeholder="Notes from earlier rounds, interviewer names, topics already covered…"
        className="w-full p-2 rounded bg-[var(--color-bg)] border border-white/10 text-sm"
      />
      <button
        type="button"
        onClick={() => generate.mutate()}
        disabled={generate.isPending}
        className="min-h-[44px] px-4 rounded text-sm border border-white/20 bg-white/5 disabled:opacity-50"
      >
        {generate.isPending
          ? 'Building prep pack…'
          : pack
            ? 'Regenerate prep pack'
            : 'Build prep pack'}
      </button>
      <div className="rounded border border-white/10 p-3 space-y-2">
        <p className="text-sm font-medium">Company and interviewer research</p>
        <input
          value={interviewer}
          onChange={e => setInterviewer(e.target.value)}
          placeholder="Optional interviewer name"
          className="w-full min-h-[44px] p-2 rounded bg-[var(--color-bg)] border border-white/10 text-sm"
        />
        <button
          type="button"
          onClick={() => research.mutate()}
          disabled={research.isPending}
          className="min-h-[44px] px-3 rounded text-sm border border-white/20 bg-white/5 disabled:opacity-50"
        >
          {research.isPending
            ? 'Verifying sources…'
            : findings
              ? 'Refresh research'
              : 'Research with verified sources'}
        </button>
        {research.isError && (
          <p className="text-xs text-red-400">
            {(research.error as Error).message}
          </p>
        )}
        {findings?.facts.map((fact, index) => (
          <div key={`${fact.claim}-${index}`} className="text-sm">
            <p>{fact.claim}</p>
            <p className="text-xs">
              {fact.sources.map((source, i) => (
                <span key={source.url}>
                  {i > 0 && ' · '}
                  <a
                    href={source.url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="text-[var(--color-primary)] hover:underline"
                  >
                    {source.title || 'Source'} ↗
                  </a>
                </span>
              ))}
            </p>
          </div>
        ))}
      </div>
      {generate.isError && (
        <p className="text-sm text-red-400">
          {(generate.error as Error).message}
        </p>
      )}
      {pack && (
        <>
          <div className="rounded border border-white/10 p-3">
            <p className="text-xs text-[var(--color-text-muted)]">
              Opening pitch
            </p>
            <p className="text-sm whitespace-pre-wrap">{pack.openingPitch}</p>
          </div>
          {pack.watchouts.length > 0 && (
            <div className="rounded border border-amber-500/30 p-3">
              <p className="text-xs text-amber-300 mb-1">Watchouts</p>
              <ul className="text-sm list-disc pl-5">
                {pack.watchouts.map(x => (
                  <li key={x}>{x}</li>
                ))}
              </ul>
            </div>
          )}
          {pack.questions.map((question, index) => (
            <div
              key={`${question.question}-${index}`}
              className="rounded border border-white/10 p-3 space-y-2"
            >
              <p className="text-sm font-medium">{question.question}</p>
              <p className="text-xs text-[var(--color-text-muted)]">
                Why: {question.whyAsked}
              </p>
              {question.stories.map(story => (
                <p
                  key={story.id}
                  className="text-sm border-l-2 border-emerald-500/40 pl-2"
                >
                  {story.text}
                </p>
              ))}
              {question.gap && (
                <p className="text-xs text-amber-300">Gap: {question.gap}</p>
              )}
              {question.bridge && (
                <p className="text-sm">Honest bridge: {question.bridge}</p>
              )}
            </div>
          ))}
          <div className="rounded border border-white/10 p-3">
            <p className="text-xs text-[var(--color-text-muted)] mb-1">
              Questions for them
            </p>
            <ul className="text-sm list-disc pl-5">
              {pack.questionsForThem.map(x => (
                <li key={x}>{x}</li>
              ))}
            </ul>
          </div>
        </>
      )}
    </div>
  );
}

export function ApplicationDetail({
  applicationId,
  onBack,
}: {
  applicationId: string;
  onBack: () => void;
}) {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<
    'resume' | 'answers' | 'recorded' | 'prep' | 'trail'
  >('resume');
  const [steer, setSteer] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['jobs', 'application', applicationId],
    queryFn: () => api.jobs.applications.get(applicationId),
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['jobs'] });
  };

  const tailor = useMutation({
    mutationFn: (nextSteer: string) =>
      api.jobs.applications.tailor(applicationId, nextSteer),
    onSuccess: invalidate,
  });

  const setStatus = useMutation({
    mutationFn: (status: ApplicationStatus) =>
      api.jobs.applications.update(applicationId, { status }),
    onSuccess: invalidate,
  });

  const submit = useMutation({
    mutationFn: () => api.jobs.applications.submit(applicationId),
    onSuccess: invalidate,
  });
  const generateLetter = useMutation({
    mutationFn: () =>
      api.jobs.applications.generateCoverLetter(applicationId, effectiveSteer),
    onSuccess: invalidate,
  });

  if (isLoading || !data) {
    return (
      <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)]">
        Loading…
      </div>
    );
  }

  const effectiveSteer = steer ?? data.steer;
  const latest = data.resumes[0] ?? null;
  const content = tailor.data?.content ?? latest?.content ?? null;
  const purgeDays = daysUntilPurge(data.purgeAfter);

  return (
    <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
      <MasterDetailBack onClick={onBack} label="Applications" />

      <div className="p-4 overflow-y-auto space-y-4">
        <div>
          <h2 className="text-lg font-semibold text-[var(--color-text)]">
            {data.title}
          </h2>
          <p className="text-sm text-[var(--color-text-muted)]">
            {data.company}
            {data.location && ` · ${data.location}`}
          </p>
          {data.jobUrl && (
            <a
              href={data.jobUrl}
              target="_blank"
              rel="noreferrer noopener"
              className="text-xs text-[var(--color-primary)] hover:underline"
            >
              Open the posting ↗
            </a>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <select
            value={data.status}
            onChange={e =>
              setStatus.mutate(e.target.value as ApplicationStatus)
            }
            className="min-h-[44px] px-2 rounded bg-[var(--color-bg)] border border-white/10 text-sm text-[var(--color-text)]"
          >
            {PIPELINE_ORDER.map(status => (
              <option key={status} value={status}>
                {STATUS_LABELS[status]}
              </option>
            ))}
          </select>

          {!data.appliedAt && (
            <button
              type="button"
              onClick={() => submit.mutate()}
              disabled={submit.isPending}
              className="min-h-[44px] px-4 rounded text-sm border border-white/20 bg-white/5 hover:bg-white/10 disabled:opacity-50"
            >
              {submit.isPending ? 'Marking…' : 'Mark as sent'}
            </button>
          )}
        </div>

        {purgeDays !== null && !data.purgedAt && (
          <p className="text-xs text-[var(--color-text-muted)]">
            Tailored files are deleted in {purgeDays} day
            {purgeDays === 1 ? '' : 's'}; the record of what you sent is kept.
          </p>
        )}

        {data.appliedAt && (
          <NoteDraft applicationId={applicationId} status={data.status} />
        )}

        <div className="rounded-lg border border-white/10 bg-[var(--color-surface)] p-3 space-y-2">
          <label className="flex items-center gap-2 text-sm min-h-[44px]">
            <input
              type="checkbox"
              checked={Boolean(data.coverLetterRequired)}
              onChange={e =>
                api.jobs.applications
                  .update(applicationId, {
                    coverLetterRequired: e.target.checked,
                  })
                  .then(invalidate)
              }
            />
            This application requires a cover letter
          </label>
          {data.coverLetterRequired && (
            <>
              <button
                type="button"
                onClick={() => generateLetter.mutate()}
                disabled={generateLetter.isPending}
                className="min-h-[44px] px-3 rounded text-sm border border-white/20 bg-white/5 disabled:opacity-50"
              >
                {generateLetter.isPending
                  ? 'Drafting…'
                  : data.coverLetter
                    ? 'Regenerate cover letter'
                    : 'Draft cover letter'}
              </button>
              {generateLetter.isError && (
                <p className="text-xs text-red-400">
                  {(generateLetter.error as Error).message}
                </p>
              )}
              {data.coverLetter && (
                <textarea
                  value={data.coverLetter}
                  rows={10}
                  onChange={e =>
                    queryClient.setQueryData(
                      ['jobs', 'application', applicationId],
                      { ...data, coverLetter: e.target.value }
                    )
                  }
                  onBlur={e =>
                    api.jobs.applications.update(applicationId, {
                      coverLetter: e.target.value,
                    })
                  }
                  className="w-full p-2 rounded bg-[var(--color-bg)] border border-white/10 text-sm"
                />
              )}
            </>
          )}
          {!data.coverLetterRequired && (
            <p className="text-xs text-[var(--color-text-muted)]">
              Optional letters are skipped by default, so no model call is
              spent.
            </p>
          )}
        </div>
        {data.purgedAt && (
          <p className="text-xs text-[var(--color-text-muted)]">
            The rendered files were deleted under the retention policy. What you
            sent is still recorded below.
          </p>
        )}

        <div className="flex gap-1 border-b border-white/10">
          {(
            [
              ['resume', 'Resume'],
              ['answers', 'Answer kit'],
              ['recorded', `Answered (${data.recordedAnswers.length})`],
              ['prep', 'Interview prep'],
              ['trail', `Emails (${data.emails.length})`],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              onClick={() => setTab(key)}
              className={`px-3 py-2 text-sm transition-colors ${
                tab === key
                  ? 'text-[var(--color-primary)] border-b-2 border-[var(--color-primary)]'
                  : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {tab === 'resume' && (
          <div className="space-y-3">
            <SteerBar
              steer={effectiveSteer}
              onSteerChange={setSteer}
              onRun={nextSteer => tailor.mutate(nextSteer)}
              busy={tailor.isPending}
              runLabel={latest ? 'Re-tailor resume' : 'Tailor resume'}
            />

            {tailor.isError && (
              <p className="text-sm text-red-400">
                {(tailor.error as Error).message}
              </p>
            )}

            {content && <KeywordBlock content={content} />}
            {(tailor.data?.review || latest?.review) && (
              <AtsReview review={(tailor.data?.review || latest?.review)!} />
            )}
            {content && <RewriteReview content={content} />}
            {content && latest && !latest.purgedAt && (
              <ResumeEditor
                version={latest}
                content={content}
                disabled={Boolean(data.appliedAt)}
                onSaved={invalidate}
              />
            )}

            {latest && !latest.purgedAt && (
              <div className="flex gap-2">
                {latest.pdfPath && (
                  <a
                    href={api.jobs.resumes.downloadUrl(latest.id, 'pdf')}
                    className="min-h-[44px] flex items-center px-4 rounded text-sm border border-white/20 bg-white/5 hover:bg-white/10"
                  >
                    Download PDF
                  </a>
                )}
                {latest.docxPath && (
                  <a
                    href={api.jobs.resumes.downloadUrl(latest.id, 'docx')}
                    className="min-h-[44px] flex items-center px-4 rounded text-sm border border-white/20 bg-white/5 hover:bg-white/10"
                  >
                    Download DOCX
                  </a>
                )}
              </div>
            )}

            {(tailor.data?.html || latest?.html) && (
              <div
                className="rounded-lg bg-white p-4 overflow-x-auto"
                // Rendered from our own template in backend/jobs/render.py,
                // which escapes every user field — the same trusted-markup
                // contract as the fanfic reader.
                dangerouslySetInnerHTML={{
                  __html: tailor.data?.html || latest?.html || '',
                }}
              />
            )}
          </div>
        )}

        {tab === 'answers' && (
          <AnswerKit
            applicationId={applicationId}
            steer={effectiveSteer}
            onSteerChange={setSteer}
          />
        )}

        {tab === 'recorded' && (
          <RecordedAnswers
            applicationId={applicationId}
            answers={data.recordedAnswers}
            onChanged={invalidate}
          />
        )}

        {tab === 'prep' && <InterviewPrep applicationId={applicationId} />}

        {tab === 'trail' && (
          <div className="space-y-2">
            {data.fillRuns.map(run => (
              <details
                key={run.id}
                className="rounded-lg border border-white/10 bg-[var(--color-surface)] p-3"
              >
                <summary className="text-sm cursor-pointer">
                  Filled {run.fields.length} fields ·{' '}
                  {run.pageTitle || run.pageUrl || 'application page'} ·{' '}
                  {new Date(run.createdAt).toLocaleString()}
                </summary>
                <div className="mt-2 space-y-1">
                  {run.screenshotUrl && (
                    <a
                      href={run.screenshotUrl}
                      target="_blank"
                      rel="noreferrer noopener"
                    >
                      <img
                        src={run.screenshotUrl}
                        alt="Captured application page"
                        className="max-h-64 rounded border border-white/10"
                      />
                    </a>
                  )}
                  {run.fields.map((field, index) => (
                    <p key={`${field.label}-${index}`} className="text-xs">
                      <span className="text-[var(--color-text-muted)]">
                        {field.label}:
                      </span>{' '}
                      {field.answer}
                    </p>
                  ))}
                </div>
              </details>
            ))}
            {data.emails.length === 0 ? (
              <p className="text-sm text-[var(--color-text-muted)]">
                No email linked yet. Replies are matched automatically as they
                sync.
              </p>
            ) : (
              data.emails.map(email => (
                <div
                  key={email.id}
                  className="rounded-lg border border-white/10 bg-[var(--color-surface)] p-3"
                >
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-sm text-[var(--color-text)] truncate">
                      {email.subject || '(no subject)'}
                    </p>
                    <span className="shrink-0 text-[10px] uppercase tracking-wide text-[var(--color-text-muted)]">
                      {email.linkKind === 'auto'
                        ? 'auto-linked'
                        : 'linked by you'}
                    </span>
                  </div>
                  <p className="text-xs text-[var(--color-text-muted)]">
                    {email.senderEmail} ·{' '}
                    {new Date(email.receivedAt).toLocaleDateString()}
                  </p>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}
