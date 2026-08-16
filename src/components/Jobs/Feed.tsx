import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type FeedJob } from '@/hooks/api';
import {
  formatSalary,
  isPartialScore,
  matchBand,
  matchPercent,
  SOURCE_LABELS,
  splitFeed,
  topGaps,
} from '@/lib/jobs';
import { SourcesPanel } from './SourcesPanel';

/**
 * The triage screen, designed for a phone.
 *
 * One posting per card, and the two actions at the bottom of each where a
 * thumb reaches. Queue returns immediately — the resume is built in the
 * background by backend/jobs/queue.py — so a decision costs a tap, not a wait.
 */
export function Feed() {
  const { data: jobs, isLoading } = useQuery({
    queryKey: ['jobs', 'feed'],
    queryFn: () => api.jobs.feed(),
  });
  const { data: queue } = useQuery({
    queryKey: ['jobs', 'queueStatus'],
    queryFn: api.jobs.queueStatus,
    refetchInterval: 15_000,
  });

  const { promising, rest } = splitFeed(jobs ?? []);

  return (
    <div className="flex-1 overflow-y-auto min-w-0 space-y-3">
      <SourcesPanel />
      <AddJob />

      {queue && (queue.pending > 0 || queue.failed > 0) && (
        <div className="rounded-lg border border-white/10 bg-[var(--color-surface)] p-3 text-xs text-[var(--color-text-muted)]">
          {queue.pending > 0 && (
            <span>
              {queue.pending} resume{queue.pending === 1 ? '' : 's'} building in
              the background
              {queue.running && ' · one running now'}
            </span>
          )}
          {queue.failed > 0 && (
            <span className="text-red-400">
              {queue.pending > 0 && ' · '}
              {queue.failed} failed — re-queue to retry
            </span>
          )}
        </div>
      )}

      {isLoading && (
        <p className="text-sm text-[var(--color-text-muted)]">Loading…</p>
      )}

      {!isLoading && (jobs ?? []).length === 0 && (
        <p className="text-sm text-[var(--color-text-muted)]">
          Nothing to triage. Add a source above, or paste a posting from the
          Pipeline tab.
        </p>
      )}

      {promising.length > 0 && (
        <Section label="Worth a look" jobs={promising} />
      )}
      {rest.length > 0 && (
        <Section
          label={promising.length > 0 ? 'The rest' : 'Postings'}
          jobs={rest}
        />
      )}
    </div>
  );
}

function Section({ label, jobs }: { label: string; jobs: FeedJob[] }) {
  return (
    <div className="space-y-3">
      <h3 className="text-xs uppercase tracking-wide text-[var(--color-text-muted)]">
        {label} ({jobs.length})
      </h3>
      {jobs.map(job => (
        <FeedCard key={job.id} job={job} />
      ))}
    </div>
  );
}

/** Manual entry, kept alongside the sources: a posting a friend sends you is
 * still a posting, and it lands in the same triage flow. */
function AddJob() {
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState('');
  const [text, setText] = useState('');
  const queryClient = useQueryClient();

  const create = useMutation({
    mutationFn: () => api.jobs.create(url.trim() ? { url } : { text }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['jobs'] });
      setUrl('');
      setText('');
      setOpen(false);
    },
  });

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="min-h-[44px] px-4 rounded text-sm border border-white/20 bg-white/5 hover:bg-white/10"
      >
        Add a posting by hand
      </button>
    );
  }

  return (
    <div className="rounded-lg border border-white/10 bg-[var(--color-surface)] p-3 space-y-2">
      <input
        value={url}
        onChange={e => setUrl(e.target.value)}
        placeholder="Paste the posting URL…"
        className="w-full min-h-[44px] p-2 rounded bg-[var(--color-bg)] border border-white/10 text-sm text-[var(--color-text)]"
      />
      <p className="text-xs text-[var(--color-text-muted)]">
        or paste the posting text (for pages that need a login)
      </p>
      <textarea
        value={text}
        onChange={e => setText(e.target.value)}
        rows={4}
        className="w-full p-2 rounded bg-[var(--color-bg)] border border-white/10 text-sm text-[var(--color-text)] resize-y"
      />
      {create.isError && (
        <p className="text-sm text-red-400">
          {(create.error as Error).message}
        </p>
      )}
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => create.mutate()}
          disabled={create.isPending || (!url.trim() && !text.trim())}
          className="min-h-[44px] px-4 rounded text-sm bg-[var(--color-primary)]/20 text-[var(--color-primary)] border border-[var(--color-primary)]/40 disabled:opacity-50"
        >
          {create.isPending ? 'Reading…' : 'Add'}
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="min-h-[44px] px-4 rounded text-sm border border-white/20 bg-white/5"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

const BAND_CLASS = {
  strong: 'bg-emerald-500/70',
  fair: 'bg-amber-500/70',
  weak: 'bg-white/30',
  none: 'bg-white/10',
} as const;

function FeedCard({ job }: { job: FeedJob }) {
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState(false);

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['jobs'] });
  };

  const queue = useMutation({
    mutationFn: () => api.jobs.queue(job.id),
    onSuccess: refresh,
  });
  const dismiss = useMutation({
    mutationFn: () => api.jobs.dismiss(job.id),
    onSuccess: refresh,
  });
  const rationale = useMutation({
    mutationFn: () => api.jobs.rationale(job.id),
  });

  const percent = matchPercent(job.matchReasons);
  const gaps = topGaps(job.matchReasons);
  const salary = formatSalary(job.salaryMin, job.salaryMax, job.salaryCurrency);
  const assessment = rationale.data ?? job.matchReasons?.assessment;

  return (
    <div className="rounded-lg border border-white/10 bg-[var(--color-surface)] p-3 space-y-2">
      <div>
        <p className="text-sm font-medium text-[var(--color-text)]">
          {job.title}
        </p>
        <p className="text-xs text-[var(--color-text-muted)]">
          {job.company}
          {job.location && ` · ${job.location}`}
          {job.remote && ' · Remote'}
          {salary && ` · ${salary}`}
        </p>
      </div>

      {/* The coverage bar is the whole point of the card: it is computed, free,
          and it is what the feed is ordered by. */}
      <div className="flex items-center gap-2">
        <div className="flex-1 h-1.5 rounded bg-white/10 overflow-hidden">
          <div
            className={`h-full ${BAND_CLASS[matchBand(percent)]}`}
            style={{ width: `${percent ?? 0}%` }}
          />
        </div>
        <span className="text-xs text-[var(--color-text-muted)] tabular-nums">
          {percent == null ? 'unscored' : `${percent}%`}
          {isPartialScore(job.matchReasons) && '*'}
        </span>
      </div>

      {gaps.length > 0 && (
        <p className="text-xs text-[var(--color-text-muted)]">
          Missing:{' '}
          <span className="text-[var(--color-text)]">{gaps.join(', ')}</span>
        </p>
      )}

      {isPartialScore(job.matchReasons) && (
        <p className="text-xs text-[var(--color-text-muted)]">
          * scored from a summary, not the full posting
        </p>
      )}

      {expanded && (
        <p className="text-xs text-[var(--color-text-muted)] whitespace-pre-wrap">
          {job.description}
        </p>
      )}

      {assessment && (
        <p className="text-xs text-[var(--color-text)] border-l-2 border-white/20 pl-2">
          {assessment.rationale}
          {assessment.angle && (
            <span className="block mt-1 text-[var(--color-text-muted)]">
              Lead with: {assessment.angle}
            </span>
          )}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2 pt-1">
        <button
          type="button"
          onClick={() => queue.mutate()}
          disabled={queue.isPending}
          className="flex-1 min-w-[100px] min-h-[44px] px-3 rounded text-sm bg-[var(--color-primary)]/20 text-[var(--color-primary)] border border-[var(--color-primary)]/40 disabled:opacity-50"
        >
          {queue.isPending ? 'Queueing…' : 'Queue'}
        </button>
        <button
          type="button"
          onClick={() => dismiss.mutate()}
          disabled={dismiss.isPending}
          className="flex-1 min-w-[100px] min-h-[44px] px-3 rounded text-sm border border-white/20 bg-white/5 hover:bg-white/10 disabled:opacity-50"
        >
          Dismiss
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-3 text-xs">
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          className="text-[var(--color-text-muted)] hover:text-[var(--color-text)] min-h-[36px]"
        >
          {expanded ? 'Less' : 'Read posting'}
        </button>
        {!assessment && (
          <button
            type="button"
            onClick={() => rationale.mutate()}
            disabled={rationale.isPending}
            className="text-[var(--color-text-muted)] hover:text-[var(--color-text)] min-h-[36px] disabled:opacity-50"
          >
            {rationale.isPending ? 'Thinking…' : 'Ask if it is worth it'}
          </button>
        )}
        {job.url && (
          <a
            href={job.url}
            target="_blank"
            rel="noreferrer"
            className="text-[var(--color-text-muted)] hover:text-[var(--color-text)] min-h-[36px]"
          >
            Open ↗
          </a>
        )}
        <span className="text-[var(--color-text-muted)] ml-auto">
          {SOURCE_LABELS[job.source]}
        </span>
      </div>

      {rationale.isError && (
        <p className="text-xs text-red-400">
          {(rationale.error as Error).message}
        </p>
      )}
    </div>
  );
}
