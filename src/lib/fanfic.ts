// Pure helpers for the fanfic library view (unit-tested in node, no DOM).

import type { FicChapterSummary } from '@/hooks/api';

export type FicSite =
  | 'spacebattles'
  | 'sufficientvelocity'
  | 'questionablequesting'
  | 'fanfiction'
  | 'ao3'
  | 'patreon';

const SITE_HOSTS: Record<string, FicSite> = {
  'fanfiction.net': 'fanfiction',
  'm.fanfiction.net': 'fanfiction',
  'archiveofourown.org': 'ao3',
  'patreon.com': 'patreon',
  'forums.spacebattles.com': 'spacebattles',
  'forums.sufficientvelocity.com': 'sufficientvelocity',
  'forum.questionablequesting.com': 'questionablequesting',
};

export const SITE_LABELS: Record<FicSite, string> = {
  fanfiction: 'FanFiction.net',
  ao3: 'AO3',
  patreon: 'Patreon',
  spacebattles: 'SpaceBattles',
  sufficientvelocity: 'Sufficient Velocity',
  questionablequesting: 'Questionable Questing',
};

/** Human label for a stored fic's `site` hostname (e.g. "SpaceBattles"). */
export function siteLabel(host: string | null): string | null {
  if (!host) return null;
  const site = SITE_HOSTS[host.startsWith('www.') ? host.slice(4) : host];
  return site ? SITE_LABELS[site] : host;
}

export function detectFicSite(url: string): FicSite | null {
  let host: string;
  try {
    host = new URL(url).hostname.toLowerCase();
  } catch {
    return null;
  }
  if (host.startsWith('www.')) host = host.slice(4);
  return SITE_HOSTS[host] ?? null;
}

/** Main-story categories sort before sidestory/apocrypha/etc. Matches the
 * backend chapter-list ordering. */
function categoryRank(category: string): number {
  const c = category.toLowerCase();
  return c === 'threadmarks' || c === 'chapters' ? 0 : 1;
}

export function orderChapters(
  chapters: FicChapterSummary[]
): FicChapterSummary[] {
  return [...chapters].sort(
    (a, b) =>
      categoryRank(a.category) - categoryRank(b.category) ||
      a.category.localeCompare(b.category) ||
      a.position - b.position
  );
}

export function adjacentChapter(
  chapters: FicChapterSummary[],
  currentId: string,
  dir: 1 | -1
): FicChapterSummary | null {
  const ordered = orderChapters(chapters);
  const idx = ordered.findIndex(c => c.id === currentId);
  if (idx === -1) return null;
  return ordered[idx + dir] ?? null;
}

/** Group ordered chapters by category, preserving order. */
export function groupChaptersByCategory(
  chapters: FicChapterSummary[]
): [string, FicChapterSummary[]][] {
  const groups: [string, FicChapterSummary[]][] = [];
  for (const ch of orderChapters(chapters)) {
    const last = groups[groups.length - 1];
    if (last && last[0] === ch.category) last[1].push(ch);
    else groups.push([ch.category, [ch]]);
  }
  return groups;
}

/** Ids of all chapters up to and including the target, in reading order —
 * powers "mark read up to here". Unknown target returns []. */
export function chapterIdsUpTo(
  chapters: FicChapterSummary[],
  chapterId: string
): string[] {
  const ordered = orderChapters(chapters);
  const idx = ordered.findIndex(c => c.id === chapterId);
  if (idx === -1) return [];
  return ordered.slice(0, idx + 1).map(c => c.id);
}

/** "★★★☆☆"-style string for a 1–5 rating; null when unrated. */
export function formatRating(rating: number | null | undefined): string | null {
  if (!rating || rating < 1 || rating > 5) return null;
  return '★'.repeat(rating) + '☆'.repeat(5 - rating);
}

export interface ScanStatus {
  label: string;
  /** A failed page fetch the scan will come back to on its own, rather than
   * something the user has to act on. */
  retrying: boolean;
}

/** What a collection scan's row should say it is doing.
 *
 * A scan waiting out a transient fetch failure is still `pending` — it holds
 * its page position and the worker resumes it — but it carries the error that
 * deferred it. Reporting that as plain "Scanning" hides a stall, and reporting
 * it as a failure invites a pointless restart, so it gets its own state.
 */
export function scanStatus(scan: {
  status: 'pending' | 'complete' | 'error';
  error: string | null;
  retryAfter?: number;
}): ScanStatus {
  if (scan.status === 'complete')
    return { label: 'Scan complete', retrying: false };
  if (scan.status === 'error') return { label: 'Stopped', retrying: false };
  const due = (scan.retryAfter ?? 0) * 1000;
  if (scan.error && due > Date.now()) {
    return {
      label: `Retrying at ${new Date(due).toLocaleTimeString()}`,
      retrying: true,
    };
  }
  return { label: 'Scanning', retrying: false };
}
