import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/hooks/api';
import type { ApplicationStatus, TailoredContent } from '@/hooks/api';
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

export function ApplicationDetail({
  applicationId,
  onBack,
}: {
  applicationId: string;
  onBack: () => void;
}) {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<'resume' | 'answers' | 'trail'>('resume');
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
            {content && <RewriteReview content={content} />}

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

        {tab === 'trail' && (
          <div className="space-y-2">
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
