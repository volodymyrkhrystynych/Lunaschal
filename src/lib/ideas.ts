// Pure logic for the Ideas tab, kept out of the components so it can be tested
// in the node environment (see the testing note in CLAUDE.md).
import type { IdeaStatus, IdeaSummary, IdeaVerdict } from '../hooks/api';

/** Display order and labels for the idea lifecycle. */
export const IDEA_STATUSES: { value: IdeaStatus; label: string }[] = [
  { value: 'new', label: 'New' },
  { value: 'researching', label: 'Researching' },
  { value: 'ready', label: 'Ready' },
  { value: 'planned', label: 'Planned' },
  { value: 'building', label: 'Building' },
  { value: 'shipped', label: 'Shipped' },
  { value: 'parked', label: 'Parked' },
];

export function statusLabel(status: IdeaStatus): string {
  return IDEA_STATUSES.find(s => s.value === status)?.label ?? status;
}

/**
 * Tailwind classes per status. Shipped and parked are deliberately muted: the
 * list is a working queue, and finished or shelved items should recede rather
 * than compete with what's live.
 */
export function statusClasses(status: IdeaStatus): string {
  switch (status) {
    case 'researching':
      return 'bg-sky-500/20 text-sky-300';
    case 'ready':
      return 'bg-emerald-500/20 text-emerald-300';
    case 'planned':
      return 'bg-violet-500/20 text-violet-300';
    case 'building':
      return 'bg-amber-500/20 text-amber-300';
    case 'shipped':
      return 'bg-white/10 text-[var(--color-text-muted)]';
    case 'parked':
      return 'bg-white/5 text-[var(--color-text-muted)]';
    default:
      return 'bg-[var(--color-primary)]/20 text-[var(--color-primary)]';
  }
}

/** Tags are stored as a JSON-array TEXT column; NULL and malformed both mean none. */
export function parseTags(raw: string | null | undefined): string[] {
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (t): t is string => typeof t === 'string' && t.length > 0
    );
  } catch {
    return [];
  }
}

/**
 * A title for an idea that has only ever been dictated. The backend leaves
 * `title` empty on voice capture and fills it in later, so the list needs
 * something to show in the meantime — the first line, clipped on a word
 * boundary rather than mid-word.
 */
export function displayTitle(
  idea: { title: string; rawContent?: string },
  limit = 60
): string {
  const title = idea.title.trim();
  if (title) return title;

  const firstLine = (idea.rawContent ?? '').trim().split('\n')[0]?.trim() ?? '';
  if (!firstLine) return 'Untitled idea';
  if (firstLine.length <= limit) return firstLine;

  const clipped = firstLine.slice(0, limit);
  const lastSpace = clipped.lastIndexOf(' ');
  return `${(lastSpace > limit / 2 ? clipped.slice(0, lastSpace) : clipped).trimEnd()}…`;
}

export interface IdeaFilter {
  status?: IdeaStatus | 'all';
  tag?: string;
  query?: string;
  /**
   * Which repository's ideas to show. 'all' (or undefined) shows every idea,
   * including the ones belonging to no repo — the list is a personal backlog,
   * and hiding rows by default is how an idea gets forgotten.
   */
  repoId?: string | 'all';
}

/**
 * Filter the list client-side. The corpus is a personal backlog — tens of rows,
 * already fetched — so a server round-trip per keystroke would be slower and
 * would not work offline.
 */
export function filterIdeas(
  ideas: IdeaSummary[],
  filter: IdeaFilter
): IdeaSummary[] {
  const q = filter.query?.trim().toLowerCase() ?? '';
  return ideas.filter(idea => {
    if (
      filter.status &&
      filter.status !== 'all' &&
      idea.status !== filter.status
    ) {
      return false;
    }
    if (filter.tag && !parseTags(idea.tags).includes(filter.tag)) return false;
    if (
      filter.repoId &&
      filter.repoId !== 'all' &&
      idea.repoId !== filter.repoId
    ) {
      return false;
    }
    if (q && !idea.title.toLowerCase().includes(q)) return false;
    return true;
  });
}

export interface Implementation {
  verdict: IdeaVerdict | null;
  /** Who decided. The user's call always wins, and saying so is the point. */
  source: 'user' | 'agent' | null;
  /** Null when the user decided — a human verdict has no confidence score. */
  confidence: number | null;
  stale: boolean;
}

/**
 * Which "already implemented" answer to show.
 *
 * The user's own verdict always beats the agent's, and the agent's is marked
 * stale once the repo has moved past the snapshot it was formed against. An
 * assessment presented as current when it isn't is exactly how this feature
 * would start lying.
 */
export function resolveImplementation(idea: {
  userVerdict?: IdeaVerdict | null;
  verdict?: IdeaVerdict | null;
  confidence?: number | null;
  assessmentStale?: boolean;
}): Implementation {
  if (idea.userVerdict) {
    return {
      verdict: idea.userVerdict,
      source: 'user',
      confidence: null,
      stale: false,
    };
  }
  if (idea.verdict) {
    return {
      verdict: idea.verdict,
      source: 'agent',
      confidence: idea.confidence ?? null,
      stale: !!idea.assessmentStale,
    };
  }
  return { verdict: null, source: null, confidence: null, stale: false };
}

export function implementationLabel(impl: Implementation): string {
  if (!impl.verdict) return 'Not assessed';
  const base =
    impl.verdict === 'yes'
      ? 'Already built'
      : impl.verdict === 'partial'
        ? 'Partly built'
        : 'Not built';
  if (impl.source === 'user') return `${base} (you)`;
  const pct =
    impl.confidence == null ? '' : ` ${Math.round(impl.confidence * 100)}%`;
  return `${base}${pct}${impl.stale ? ' · stale' : ''}`;
}

export function implementationClasses(impl: Implementation): string {
  if (!impl.verdict) return 'bg-white/5 text-[var(--color-text-muted)]';
  if (impl.stale) return 'bg-white/10 text-[var(--color-text-muted)]';
  switch (impl.verdict) {
    case 'yes':
      return 'bg-emerald-500/20 text-emerald-300';
    case 'partial':
      return 'bg-amber-500/20 text-amber-300';
    default:
      return 'bg-white/10 text-[var(--color-text-muted)]';
  }
}

/** "Needs more decisions" is simply: the agent asked something unanswered. */
export function needsDecisions(idea: { openQuestionCount?: number }): boolean {
  return (idea.openQuestionCount ?? 0) > 0;
}

/**
 * The sentinel for the write-your-own row. Not prose, so it cannot collide
 * with an option the model wrote, and unlike a value with a leading space it
 * survives a round-trip through a DOM attribute.
 */
export const OTHER_CHOICE = '__other__';

export interface DecisionChoice {
  /** The answer this row submits — or OTHER_CHOICE, which submits the note. */
  value: string;
  label: string;
  /** True for the last row, which reveals a text field instead of answering. */
  isOther: boolean;
}

/**
 * A decision as radio rows: the agent's options, then a write-your-own.
 *
 * The last row is always present, and always last, which is the whole shape of
 * the control — the agent proposes the forks it can see, and the answer it did
 * not think of has to be reachable without leaving the list. A question that
 * arrived with no usable options degrades to just that row, which is exactly
 * the free-text field this used to be.
 */
export function decisionChoices(options: string[]): DecisionChoice[] {
  const seen = new Set<string>();
  const rows: DecisionChoice[] = [];
  for (const option of options) {
    const text = (option ?? '').trim();
    if (!text) continue;
    const key = text.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push({ value: text, label: text, isOther: false });
  }
  rows.push({
    value: OTHER_CHOICE,
    label: 'Something else…',
    isOther: true,
  });
  return rows;
}

/**
 * Which row a previously given answer sits on. An answer that matches an option
 * selects it; anything else was written by hand, so it belongs in the note.
 * Reopening a decision should show what was decided, not a blank form.
 */
export function selectedChoice(
  options: string[],
  answer: string | null | undefined
): { value: string; note: string } {
  const text = (answer ?? '').trim();
  if (!text) return { value: '', note: '' };
  const match = options.find(o => o.trim() === text);
  if (match) return { value: match, note: '' };
  return { value: OTHER_CHOICE, note: text };
}

/** The text a decision would submit, or '' when it isn't answerable yet. */
export function decisionAnswer(choice: string, note: string): string {
  if (!choice) return '';
  if (choice === OTHER_CHOICE) return note.trim();
  return choice;
}

export const EFFORT_LABELS: Record<string, string> = {
  s: 'Small',
  m: 'Medium',
  l: 'Large',
};

/** {name, count} pills over the fetched list, most-used first then alphabetical. */
export function tagCounts(
  ideas: IdeaSummary[]
): { name: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const idea of ideas) {
    for (const tag of parseTags(idea.tags)) {
      counts.set(tag, (counts.get(tag) ?? 0) + 1);
    }
  }
  return [...counts.entries()]
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
}
