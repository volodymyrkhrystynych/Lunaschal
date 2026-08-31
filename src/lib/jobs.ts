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
  TriageFlag,
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
 * Feed cards grouped by how close the posting is.
 *
 * The grouping is the model's (a coarse bucket), the order *inside* each group
 * is the server's — which is the deterministic keyword score. That split is
 * deliberate: a bucket is stable between refreshes in a way a model-produced
 * number is not, so the feed never reshuffles while it is being read.
 *
 * Untriaged postings fall into `rest` rather than being hidden. With the model
 * off, every row is untriaged and the feed reads exactly as it did before
 * triage existed.
 */
export function splitFeed(
  jobs: FeedJob[],
  threshold = 40
): { promising: FeedJob[]; rest: FeedJob[] } {
  const promising: FeedJob[] = [];
  const rest: FeedJob[] = [];
  for (const job of jobs) {
    if (job.triageFit) {
      if (job.triageFit === 'strong' || job.triageFit === 'possible') {
        promising.push(job);
      } else {
        rest.push(job);
      }
      continue;
    }
    // Never triaged: fall back to the keyword score, as before.
    const percent = matchPercent(job.matchReasons);
    if (percent != null && percent >= threshold) promising.push(job);
    else rest.push(job);
  }
  return { promising, rest };
}

/** Human labels for the flags `ai/job_triage.py` may raise. */
export const FLAG_LABELS: Record<TriageFlag['kind'], string> = {
  seniority_mismatch: 'Seniority mismatch',
  unpaid: 'Unpaid',
  commission_only: 'Commission only',
  unclear_role: 'Vague role',
  contract_only: 'Contract only',
  onsite_required: 'On-site required',
  security_clearance: 'Clearance required',
  heavy_travel: 'Heavy travel',
  stack_mismatch: 'Different stack',
};

/** Labels for the fit bucket, said the way a person would say it. */
export const FIT_LABELS: Record<string, string> = {
  strong: 'Worth applying',
  possible: 'Worth a look',
  stretch: 'A stretch',
};

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

// --------------------------------------------------------------------------
// Commute distance
// --------------------------------------------------------------------------

/** Where the backend measures from. Kept here so the card can name it rather
 * than showing a bare number the reader has to take on faith. */
export const DISTANCE_ANCHOR_LABEL = 'Union Station';

/** Bands for the commute annotation, sized for the GTA rather than for the
 * globe: downtown and the inner suburbs, the 905, the outer commuter belt,
 * and then everything that is really a relocation. Coarse on purpose — the
 * number is a rough signal, and `matchBand` above takes the same line. */
export function distanceBand(
  km: number | null
): 'near' | 'commutable' | 'far' | 'distant' | 'unknown' {
  if (km == null) return 'unknown';
  if (km <= 10) return 'near';
  if (km <= 30) return 'commutable';
  if (km <= 80) return 'far';
  return 'distant';
}

/**
 * The commute line for a feed card.
 *
 * Remote is answered before distance and never as a number: a remote posting
 * has no commute, and printing "0 km" for one would say something false about
 * a job three subway stops away. An unplaced location returns null so the card
 * renders nothing at all — the honest state, and the common one while 1,300
 * backfilled rows carry no location.
 */
interface CommuteFields {
  remote: boolean;
  distanceKm: number | null;
  distancePrecision?: string;
  workLocation?: string;
}

/** True when the body says attendance is expected, whatever the board's flag
 * says. This is the contradiction `work_location` exists to record. */
function attendsInPerson(job: CommuteFields): boolean {
  return job.workLocation === 'onsite' || job.workLocation === 'hybrid';
}

export function distanceLabel(job: CommuteFields): string | null {
  // Remote is answered first and never as a number — unless the body itself
  // contradicted it, in which case the commute is real and is the point.
  if (job.remote && !attendsInPerson(job)) return 'Remote';
  if (job.distanceKm == null) return job.remote ? 'Remote' : null;

  const km = job.distanceKm;
  const rounded = km < 10 ? Math.round(km * 10) / 10 : Math.round(km);
  // 'exact' came from coordinates the board posted; everything else is a city
  // or district centre, so the number gets a '~' rather than implying a
  // door-to-door measurement it cannot support.
  const prefix = job.distancePrecision === 'exact' ? '' : '~';
  // Naming the mode only where it contradicts the board's flag: it is what
  // explains why a posting listed as remote is sitting among the located ones.
  const lead =
    job.remote && attendsInPerson(job)
      ? `${job.workLocation === 'hybrid' ? 'Hybrid' : 'On-site'} · `
      : '';
  return `${lead}${prefix}${rounded} km from ${DISTANCE_ANCHOR_LABEL}`;
}

/** The band the commute pill is coloured by. A posting with no real commute
 * (remote, or unplaced) is `unknown` rather than being painted on the same
 * scale as a distance somebody would actually travel. */
export function commuteBand(job: CommuteFields) {
  if (job.remote && !attendsInPerson(job)) return distanceBand(null);
  return distanceBand(job.distanceKm);
}
