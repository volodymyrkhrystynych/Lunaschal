import type {
  KnowledgeArchive,
  KnowledgeHealth,
  KnowledgeKind,
} from '../hooks/api';

/** Display order of the source kinds, and the order the sidebar groups them in. */
export const KIND_ORDER: KnowledgeKind[] = [
  'encyclopedia',
  'qa',
  'docs',
  'other',
];

export const KIND_LABELS: Record<KnowledgeKind, string> = {
  encyclopedia: 'Encyclopedias',
  qa: 'Questions & answers',
  docs: 'Documentation',
  other: 'Other',
};

export const KIND_CHIPS: Record<KnowledgeKind, string> = {
  encyclopedia: 'wiki',
  qa: 'Q&A',
  docs: 'docs',
  other: 'other',
};

export function sizeLabel(bytes: number): string {
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(unit < 2 ? 0 : 1)} ${units[unit]}`;
}

export interface HealthBadge {
  label: string;
  /** `warn` is informational; only `error` means the archive cannot be searched. */
  tone: 'ok' | 'warn' | 'error';
  hint: string;
}

/**
 * How one archive's health should read.
 *
 * `no_fulltext` is deliberately a `warn` and not an `error`: the entire DevDocs
 * collection is published without a fulltext index, and those archives are
 * still searched — by title. Showing them as broken would be telling the user
 * to fix something that is working as designed.
 */
export function healthBadge(health: KnowledgeHealth): HealthBadge | null {
  switch (health) {
    case 'ok':
      return null;
    case 'no_fulltext':
      return {
        label: 'title search only',
        tone: 'warn',
        hint: 'Built without a fulltext index; matched on article titles.',
      };
    case 'truncated':
      return {
        label: 'incomplete',
        tone: 'error',
        hint: 'Smaller than the size it was downloaded against.',
      };
    case 'unreadable':
      return {
        label: 'unreadable',
        tone: 'error',
        hint: 'The file could not be opened as a ZIM archive.',
      };
    case 'missing':
      return {
        label: 'missing',
        tone: 'error',
        hint: 'Not found on the last scan — the drive may be unplugged.',
      };
    default:
      return null;
  }
}

/** Whether an archive will be consulted by a search right now. */
export function isSearchable(archive: KnowledgeArchive): boolean {
  return (
    archive.enabled &&
    (archive.health === 'ok' || archive.health === 'no_fulltext')
  );
}

export interface ArchiveGroup {
  kind: KnowledgeKind;
  label: string;
  archives: KnowledgeArchive[];
}

/**
 * Group archives for the sidebar, in a fixed kind order with empty kinds
 * dropped. Sorted by title inside each group rather than by filename: the
 * filename is what the old search ordered by, and it is exactly the ordering
 * that made the library look arbitrary.
 */
export function groupArchivesByKind(
  archives: KnowledgeArchive[]
): ArchiveGroup[] {
  const groups = new Map<KnowledgeKind, KnowledgeArchive[]>();
  for (const archive of archives) {
    const kind = KIND_ORDER.includes(archive.kind) ? archive.kind : 'other';
    const bucket = groups.get(kind);
    if (bucket) bucket.push(archive);
    else groups.set(kind, [archive]);
  }
  return KIND_ORDER.filter(kind => groups.get(kind)?.length).map(kind => ({
    kind,
    label: KIND_LABELS[kind],
    archives: [...(groups.get(kind) as KnowledgeArchive[])].sort((a, b) =>
      (a.title || a.filename).localeCompare(b.title || b.filename)
    ),
  }));
}

/**
 * One line summarising what a search actually reached, or null when there is
 * nothing worth saying. Only surfaced when archives were skipped — a search
 * that quietly consulted 3 of 600 archives is the failure mode this whole
 * federation exists to make visible.
 */
export function searchCoverage(found: {
  searched: number;
  skipped: number;
}): string | null {
  if (!found.skipped) return null;
  const plural = found.skipped === 1 ? 'archive' : 'archives';
  return `Searched ${found.searched}; ${found.skipped} ${plural} skipped (time budget or read error).`;
}
