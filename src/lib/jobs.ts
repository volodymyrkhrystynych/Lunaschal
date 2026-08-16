// Pure helpers for the Jobs view, extracted so they can be tested in the node
// environment without jsdom (the reason src/lib/ exists).
import type {
  ApplicationStatus,
  FilledAnswer,
  JobApplication,
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
