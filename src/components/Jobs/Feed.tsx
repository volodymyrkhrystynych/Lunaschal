import { useState } from 'react';
import {
  useMutation,
  useMutationState,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { api, type FeedJob } from '@/hooks/api';
import {
  MUTATION_KEYS,
  useJobDecide,
  type JobDecideVars,
} from '@/offline/mutationDefaults';
import {
  commuteBand,
  decidedIds,
  decisionErrors,
  distanceLabel,
  type FeedDecision,
  FIT_LABELS,
  FLAG_LABELS,
  formatSalary,
  hideDecided,
  isPartialScore,
  matchBand,
  matchPercent,
  pendingDecisionLabel,
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
 *
 * The tap is not a wait either. Both decisions go through the offline write
 * queue (`useJobDecide`), so the card leaves on the tap and the POST follows —
 * and a decision made with the backend unreachable is parked and replayed
 * rather than lost. What is on screen is therefore read from the mutation
 * queue, not from local state: a decision survives leaving this tab, and a
 * failed one hands its card back with the reason on it, which is the only case
 * where the optimism was wrong and the only case worth telling the user about.
 */
export function Feed() {
  // The sort lives in the client, not the profile: it is a way of reading the
  // same feed, not a standing preference, and a phone that reopens on "nearest
  // first" would hide the strongest matches from someone who forgot they set
  // it. Part of the query key, so switching refetches rather than reordering a
  // cached page the server ordered differently.
  const [sort, setSort] = useState<'match' | 'distance'>('match');

  const { data: jobs, isLoading } = useQuery({
    queryKey: ['jobs', 'feed', sort],
    queryFn: () => api.jobs.feed(100, sort),
  });

  const { data: queue } = useQuery({
    queryKey: ['jobs', 'queueStatus'],
    queryFn: api.jobs.queueStatus,
    refetchInterval: 15_000,
  });
  const { data: triage } = useQuery({
    queryKey: ['jobs', 'triageStatus'],
    queryFn: api.jobs.triageStatus,
    refetchInterval: 15_000,
  });

  const decide = useJobDecide();
  // Read straight from the write queue rather than from component state: a
  // decision paused offline outlives this component and even the page, and the
  // feed must keep reading as though it had already landed.
  const pending = useMutationState({
    filters: { mutationKey: MUTATION_KEYS.jobDecide, status: 'pending' },
    select: m => m.state.variables as JobDecideVars | undefined,
  });
  const failures = useMutationState({
    filters: { mutationKey: MUTATION_KEYS.jobDecide, status: 'error' },
    select: m => ({
      variables: m.state.variables as JobDecideVars | undefined,
      error: m.state.error,
    }),
  });

  const decided = decidedIds(pending);
  const visible = hideDecided(jobs ?? [], decided);
  const { promising, rest } = splitFeed(visible);
  const saving = pendingDecisionLabel(decided.size);
  const cardProps = {
    errors: decisionErrors(failures),
    onDecide: (job: FeedJob, kind: FeedDecision) =>
      decide.mutate({ jobId: job.id, kind }),
  };

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

      {!isLoading && visible.length === 0 && (
        <p className="text-sm text-[var(--color-text-muted)]">
          Nothing to triage. Add a source above, or paste a posting from the
          Pipeline tab.
        </p>
      )}

      {triage && triage.pending > 0 && (
        <div className="rounded-lg border border-white/10 bg-[var(--color-surface)] p-3 text-xs text-[var(--color-text-muted)]">
          {triage.pending} posting{triage.pending === 1 ? '' : 's'} still being
          read
          {triage.running && ' · one being read now'}
          {' — they show unsummarised until then.'}
        </div>
      )}

      {visible.length > 0 && (
        <div className="flex items-center gap-1 text-xs">
          <span className="text-[var(--color-text-muted)] mr-1">Sort</span>
          {(
            [
              ['match', 'Best match'],
              ['distance', 'Nearest'],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              onClick={() => setSort(key)}
              className={`px-2 min-h-[36px] rounded border transition-colors ${
                sort === key
                  ? 'border-[var(--color-primary)]/40 bg-[var(--color-primary)]/20 text-[var(--color-primary)]'
                  : 'border-white/10 text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
              }`}
            >
              {label}
            </button>
          ))}
          {sort === 'distance' && (
            <span className="text-[var(--color-text-muted)] ml-1">
              remote first, then nearest
            </span>
          )}
        </div>
      )}

      {promising.length > 0 && (
        <Section label="Worth a look" jobs={promising} {...cardProps} />
      )}
      {rest.length > 0 && (
        <Section
          label={promising.length > 0 ? 'The rest' : 'Postings'}
          jobs={rest}
          {...cardProps}
        />
      )}

      {/* Deliberately a footer and not a blocker: the decisions are already
          made, this only says the writing has not caught up yet. */}
      {saving && (
        <p className="text-xs text-[var(--color-text-muted)]">{saving}</p>
      )}

      <FilteredSection count={triage?.rejected ?? 0} />
    </div>
  );
}

/**
 * What triage threw out.
 *
 * Collapsed, but present. This filter discards job opportunities on a rule the
 * user never sees, so it has to be reviewable — a bad rule is otherwise
 * invisible until the search is over.
 */
function FilteredSection({ count }: { count: number }) {
  const [open, setOpen] = useState(false);
  const { data: filtered } = useQuery({
    queryKey: ['jobs', 'filtered'],
    queryFn: () => api.jobs.filtered(),
    enabled: open,
  });

  if (count === 0) return null;

  return (
    <div className="space-y-2 pt-2">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="text-xs uppercase tracking-wide text-[var(--color-text-muted)] hover:text-[var(--color-text)] min-h-[36px]"
      >
        {open ? '▾' : '▸'} Filtered out ({count})
      </button>
      {open && (
        <div className="space-y-2">
          {(filtered ?? []).map(job => (
            <FilteredCard key={job.id} job={job} />
          ))}
        </div>
      )}
    </div>
  );
}

function FilteredCard({ job }: { job: FeedJob }) {
  const queryClient = useQueryClient();
  const restore = useMutation({
    mutationFn: () => api.jobs.restoreTriage(job.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['jobs'] }),
  });

  return (
    <div className="rounded border border-white/5 bg-[var(--color-surface)] px-3 py-2 flex items-start gap-2">
      <div className="min-w-0 flex-1">
        <p className="text-xs text-[var(--color-text)] truncate">{job.title}</p>
        <p className="text-xs text-[var(--color-text-muted)] truncate">
          {job.company}
          {job.triageReason && ` · ${job.triageReason}`}
          {job.triageError && ` · failed: ${job.triageError}`}
        </p>
      </div>
      <button
        type="button"
        onClick={() => restore.mutate()}
        disabled={restore.isPending}
        className="shrink-0 min-h-[36px] px-2 rounded text-xs border border-white/20 bg-white/5 hover:bg-white/10 disabled:opacity-50"
      >
        Restore
      </button>
    </div>
  );
}

function Section({
  label,
  jobs,
  errors,
  onDecide,
}: {
  label: string;
  jobs: FeedJob[];
  errors: Record<string, string>;
  onDecide: (job: FeedJob, kind: FeedDecision) => void;
}) {
  return (
    <div className="space-y-3">
      <h3 className="text-xs uppercase tracking-wide text-[var(--color-text-muted)]">
        {label} ({jobs.length})
      </h3>
      {jobs.map(job => (
        <FeedCard
          key={job.id}
          job={job}
          error={errors[job.id]}
          onDecide={onDecide}
        />
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

// Remote lands on `unknown` deliberately: it is not a short commute, it is the
// absence of one, and colouring it green would put it on the same scale as a
// job downtown. The pill still reads "Remote"; only the tone is neutral.
const DISTANCE_CLASS = {
  near: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
  commutable: 'bg-sky-500/15 text-sky-300 border-sky-500/30',
  far: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
  distant: 'bg-rose-500/15 text-rose-300 border-rose-500/30',
  unknown: 'bg-white/5 text-[var(--color-text-muted)] border-white/15',
} as const;

function FeedCard({
  job,
  error,
  onDecide,
}: {
  job: FeedJob;
  /** Set only when this card's own decision came back as a failure. */
  error?: string;
  onDecide: (job: FeedJob, kind: FeedDecision) => void;
}) {
  const [expanded, setExpanded] = useState(false);

  const rationale = useMutation({
    mutationFn: () => api.jobs.rationale(job.id),
  });

  const percent = matchPercent(job.matchReasons);
  const gaps = topGaps(job.matchReasons);
  const salary = formatSalary(job.salaryMin, job.salaryMax, job.salaryCurrency);
  const commute = distanceLabel(job);
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
          {salary && ` · ${salary}`}
        </p>
      </div>

      {/* The condensed posting. This is what the card is *for*: two sentences
          meant to be decided from without opening the original, which is the
          whole reason a model reads every posting at sync time. */}
      {job.triageSummary && (
        <p className="text-xs text-[var(--color-text)] leading-relaxed">
          {job.triageSummary}
        </p>
      )}

      {(job.triageFlags.length > 0 || commute) && (
        <div className="flex flex-wrap gap-1">
          {/* The commute flag sits with the triage flags because it is read the
              same way — one glance, one decision — even though it is computed
              here rather than judged by the model. A posting the gazetteer
              could not place shows nothing at all rather than a hedge. */}
          {commute && (
            <span
              title={`Straight-line distance, ${job.distancePrecision || 'unknown'} precision`}
              className={`px-1.5 py-0.5 rounded text-[11px] border ${
                DISTANCE_CLASS[commuteBand(job)]
              }`}
            >
              {commute}
            </span>
          )}
          {job.triageFlags.map(flag => (
            <span
              key={flag.kind}
              title={flag.detail}
              className="px-1.5 py-0.5 rounded text-[11px] bg-amber-500/15 text-amber-300 border border-amber-500/30"
            >
              {FLAG_LABELS[flag.kind] ?? flag.kind}
            </span>
          ))}
        </div>
      )}

      {job.triageState === 'pending' && (
        <p className="text-xs text-[var(--color-text-muted)] italic">
          Not read yet
        </p>
      )}

      {/* The coverage bar stays, and still orders the feed within a bucket: it
          is computed, free, and stable between refreshes. */}
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
        {job.triageFit && (
          <span className="text-xs text-[var(--color-text-muted)]">
            {FIT_LABELS[job.triageFit]}
          </span>
        )}
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

      {/* Neither button is ever disabled or busy: the card is gone by the time
          a second tap could land, so there is nothing to guard against. */}
      <div className="flex flex-wrap items-center gap-2 pt-1">
        <button
          type="button"
          onClick={() => onDecide(job, 'queue')}
          className="flex-1 min-w-[100px] min-h-[44px] px-3 rounded text-sm bg-[var(--color-primary)]/20 text-[var(--color-primary)] border border-[var(--color-primary)]/40"
        >
          Queue
        </button>
        <button
          type="button"
          onClick={() => onDecide(job, 'dismiss')}
          className="flex-1 min-w-[100px] min-h-[44px] px-3 rounded text-sm border border-white/20 bg-white/5 hover:bg-white/10"
        >
          Dismiss
        </button>
      </div>

      {/* The card came back. Say why, on the card, where the retry is. */}
      {error && <p className="text-xs text-red-400">{error}</p>}

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
