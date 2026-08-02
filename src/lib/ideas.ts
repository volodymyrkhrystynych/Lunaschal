// Pure logic for the Ideas tab, kept out of the components so it can be tested
// in the node environment (see the testing note in CLAUDE.md).
import type { IdeaStatus, IdeaSummary } from '../hooks/api';

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
    if (q && !idea.title.toLowerCase().includes(q)) return false;
    return true;
  });
}

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
