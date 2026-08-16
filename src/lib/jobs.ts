// Pure helpers for the Jobs view, extracted so they can be tested in the node
// environment without jsdom (the reason src/lib/ exists).
import type {
  ApplicationStatus,
  FeedJob,
  FilledAnswer,
  JobApplication,
  JobSearch,
  JobSourceKind,
  MatchReasons,
  ResumeImportPreview,
  TailoredContent,
} from '@/hooks/api';

export const STATUS_LABELS: Record<ApplicationStatus, string> = {
  draft: 'Draft',
  ready: 'Ready to send',
  submitted: 'Submitted',
  acknowledged: 'Acknowledged',
  interview: 'Interview',
  offer: 'Offer',
  rejected: 'Rejected',
  withdrawn: 'Withdrawn',
  ghosted: 'No reply',
};

// The order the pipeline board reads in. Terminal outcomes sit at the end.
export const PIPELINE_ORDER: ApplicationStatus[] = [
  'draft',
  'ready',
  'submitted',
  'acknowledged',
  'interview',
  'offer',
  'rejected',
  'withdrawn',
  'ghosted',
];

const OPEN_STATUSES = new Set<ApplicationStatus>([
  'draft',
  'ready',
  'submitted',
  'acknowledged',
  'interview',
  'offer',
]);

export function isOpen(status: ApplicationStatus): boolean {
  return OPEN_STATUSES.has(status);
}

/** Applications grouped for the board, in PIPELINE_ORDER, empty groups dropped. */
export function groupByStatus(
  applications: JobApplication[]
): { status: ApplicationStatus; label: string; items: JobApplication[] }[] {
  const buckets = new Map<ApplicationStatus, JobApplication[]>();
  for (const application of applications) {
    const list = buckets.get(application.status);
    if (list) list.push(application);
    else buckets.set(application.status, [application]);
  }
  return PIPELINE_ORDER.filter(status => buckets.get(status)?.length).map(
    status => ({
      status,
      label: STATUS_LABELS[status],
      items: buckets.get(status) ?? [],
    })
  );
}

/**
 * Days until a tailored resume is deleted, or null when nothing is scheduled.
 * Negative would mean the sweep has not caught up yet, so it clamps at 0.
 */
export function daysUntilPurge(
  purgeAfter: string | null,
  now: Date = new Date()
): number | null {
  if (!purgeAfter) return null;
  const due = new Date(purgeAfter).getTime();
  if (Number.isNaN(due)) return null;
  return Math.max(0, Math.ceil((due - now.getTime()) / 86_400_000));
}

export function formatSalary(
  min: number | null,
  max: number | null,
  currency: string
): string {
  if (min == null && max == null) return '';
  const unit = currency ? ` ${currency}` : '';
  const round = (n: number) =>
    n >= 1000 ? `${Math.round(n / 1000)}k` : `${Math.round(n)}`;
  if (min != null && max != null) return `${round(min)}–${round(max)}${unit}`;
  return `${round((min ?? max) as number)}${unit}`;
}

/** How much of what the posting asked for the profile can actually back. */
export function coveragePercent(
  content: TailoredContent | null
): number | null {
  if (!content?.keywords) return null;
  const { matched, missing } = content.keywords;
  const total = matched.length + missing.length;
  return total === 0 ? null : Math.round((matched.length / total) * 100);
}

/** Bullets the model reworded, so the UI can show the change for approval. */
export function rewrittenBullets(content: TailoredContent | null) {
  return (content?.selectedBullets ?? []).filter(b => b.rewritten);
}

/**
 * Answer Kit progress. `unanswered` is what the user still has to write, and
 * `free` is what cost no model call — the two numbers worth surfacing.
 */
export function answerSummary(answers: FilledAnswer[]) {
  const counts = { profile: 0, bank: 0, generated: 0, unanswered: 0 };
  for (const answer of answers) counts[answer.source] += 1;
  return {
    ...counts,
    total: answers.length,
    free: counts.profile + counts.bank,
    ready: answers.length - counts.unanswered,
  };
}

/**
 * Turn a pasted job form into questions.
 *
 * One question per non-empty line, because that is how someone types a form
 * they are looking at. A trailing '?' is kept — it is part of the label the
 * model is answering — but list bullets and numbering are not.
 */
export function parseQuestionList(text: string): { label: string }[] {
  return text
    .split('\n')
    .map(line => line.replace(/^\s*(?:[-*•]|\d+[.)])\s*/, '').trim())
    .filter(line => line.length > 1)
    .map(label => ({ label }));
}

// --------------------------------------------------------------------------
// Discovery feed
// --------------------------------------------------------------------------

export const SOURCE_LABELS: Record<JobSourceKind | 'manual', string> = {
  manual: 'Added by hand',
  adzuna: 'Adzuna',
  greenhouse: 'Greenhouse',
  lever: 'Lever',
  ashby: 'Ashby',
};

/** The company boards take a slug; Adzuna takes a query. */
export function sourceNeedsSlug(kind: JobSourceKind): boolean {
  return kind !== 'adzuna';
}

/**
 * A one-line summary of a saved search, for the sources panel.
 *
 * Falls back to describing the params when the user gave no label, because a
 * row reading only "Greenhouse" is useless once there are four of them.
 */
export function describeSearch(search: JobSearch): string {
  if (search.label) return search.label;
  const params = search.params ?? {};
  if (search.kind === 'adzuna') {
    const what = String(params.what ?? '').trim();
    const where = String(params.where ?? '').trim();
    return [what || 'any role', where && `in ${where}`]
      .filter(Boolean)
      .join(' ');
  }
  return String(params.slug ?? '') || SOURCE_LABELS[search.kind];
}

/**
 * What to show about a search's last run.
 *
 * A search that has been quietly failing for a week looks exactly like a
 * search with no new postings, so the error outranks the count.
 */
export function searchState(search: JobSearch): {
  tone: 'error' | 'idle' | 'ok';
  text: string;
} {
  if (search.lastError) return { tone: 'error', text: search.lastError };
  if (!search.lastRunAt) return { tone: 'idle', text: 'Not run yet' };
  const count = search.lastCount ?? 0;
  return {
    tone: 'ok',
    text: `${count} posting${count === 1 ? '' : 's'} last run`,
  };
}

/**
 * Coverage as a percentage, or null when the posting was never scored.
 *
 * Reads the stored report rather than recomputing: the number the feed sorts
 * on and the number on the card have to be the same one.
 */
export function matchPercent(reasons: MatchReasons | null): number | null {
  if (!reasons) return null;
  const total = reasons.matched.length + reasons.missing.length;
  if (total === 0) return null;
  return Math.round((reasons.matched.length / total) * 100);
}

/** Bands for the coverage bar. Deliberately coarse — the number is a sort key
 * and a rough signal, not a measurement to two decimal places. */
export function matchBand(
  percent: number | null
): 'strong' | 'fair' | 'weak' | 'none' {
  if (percent == null) return 'none';
  if (percent >= 70) return 'strong';
  if (percent >= 40) return 'fair';
  return 'weak';
}

/**
 * The gaps worth putting on a phone card.
 *
 * `keywords.py` already orders `missing` by how often the posting mentioned
 * each term, so the first few are the ones the posting cares most about.
 */
export function topGaps(reasons: MatchReasons | null, limit = 4): string[] {
  return (reasons?.missing ?? []).slice(0, limit);
}

/** True when the score came from a snippet rather than the full posting, so
 * the UI can mark it provisional instead of implying a real measurement. */
export function isPartialScore(reasons: MatchReasons | null): boolean {
  return Boolean(reasons?.partial);
}

/**
 * Feed cards grouped into "worth a look" and the rest.
 *
 * The split is presentational only — the order inside each group is the order
 * the server sent, which is the deterministic score. Nothing here re-sorts.
 */
export function splitFeed(
  jobs: FeedJob[],
  threshold = 40
): { promising: FeedJob[]; rest: FeedJob[] } {
  const promising: FeedJob[] = [];
  const rest: FeedJob[] = [];
  for (const job of jobs) {
    const percent = matchPercent(job.matchReasons);
    if (percent != null && percent >= threshold) promising.push(job);
    else rest.push(job);
  }
  return { promising, rest };
}

/**
 * What a reviewed resume import currently adds up to.
 *
 * `roles` being zero is what disables the commit button — an import with no
 * roles writes nothing but still looks like it worked.
 */
export function importSummary(preview: ResumeImportPreview): {
  roles: number;
  bullets: number;
  skills: number;
  education: number;
  label: string;
} {
  const roles = preview.roles?.length ?? 0;
  const bullets = (preview.roles ?? []).reduce(
    (n, role) => n + (role.bullets?.length ?? 0),
    0
  );
  const skills = preview.skills?.length ?? 0;
  const education = preview.education?.length ?? 0;

  const parts = [
    [roles, 'role'],
    [bullets, 'bullet'],
    [skills, 'skill'],
  ] as const;
  const label = parts
    .filter(([n]) => n > 0)
    .map(([n, word]) => `${n} ${word}${n === 1 ? '' : 's'}`)
    .join(', ');

  return {
    roles,
    bullets,
    skills,
    education,
    label: label || 'nothing selected',
  };
}

/**
 * What the desktop queue looks like: applications with a resume waiting to be
 * reviewed and sent, plus the ones whose generation failed.
 */
export function queueBreakdown(applications: JobApplication[]): {
  ready: JobApplication[];
  building: JobApplication[];
  failed: JobApplication[];
} {
  const ready: JobApplication[] = [];
  const building: JobApplication[] = [];
  const failed: JobApplication[] = [];
  for (const application of applications) {
    if (application.queueError) failed.push(application);
    else if (application.status === 'ready') ready.push(application);
    else if (application.queuedAt && application.status === 'draft')
      building.push(application);
  }
  return { ready, building, failed };
}
