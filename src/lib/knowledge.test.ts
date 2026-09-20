import { describe, expect, it } from 'vitest';
import type { KnowledgeArchive } from '@/hooks/api';
import {
  groupArchivesByKind,
  healthBadge,
  isSearchable,
  searchCoverage,
  sizeLabel,
} from './knowledge';

function archive(over: Partial<KnowledgeArchive> = {}): KnowledgeArchive {
  return {
    id: over.title ?? 'id',
    filename: 'wikipedia_en_all.zim',
    title: 'Wikipedia',
    size: 1024,
    kind: 'encyclopedia',
    enabled: true,
    health: 'ok',
    ...over,
  };
}

describe('sizeLabel', () => {
  it('drops the decimals only where they would be noise', () => {
    expect(sizeLabel(512)).toBe('512 B');
    expect(sizeLabel(2048)).toBe('2 KB');
    expect(sizeLabel(5 * 1024 * 1024)).toBe('5.0 MB');
    expect(sizeLabel(114855633920)).toBe('107.0 GB');
  });
});

describe('healthBadge', () => {
  it('says nothing about a healthy archive', () => {
    expect(healthBadge('ok')).toBeNull();
  });

  it('treats a missing fulltext index as information, not an error', () => {
    // Every DevDocs archive Kiwix publishes is built this way and is still
    // searched by title; showing it red would be telling the user to fix
    // something that is working as designed.
    expect(healthBadge('no_fulltext')?.tone).toBe('warn');
  });

  it.each(['unreadable', 'truncated', 'missing'] as const)(
    'reports %s as an error',
    health => {
      expect(healthBadge(health)?.tone).toBe('error');
    }
  );
});

describe('isSearchable', () => {
  it('counts a title-only archive as searchable and a disabled one as not', () => {
    expect(isSearchable(archive({ health: 'no_fulltext' }))).toBe(true);
    expect(isSearchable(archive({ enabled: false }))).toBe(false);
    expect(isSearchable(archive({ health: 'missing' }))).toBe(false);
  });
});

describe('groupArchivesByKind', () => {
  it('orders kinds consistently and drops empty ones', () => {
    const groups = groupArchivesByKind([
      archive({ title: 'Lit', kind: 'docs' }),
      archive({ title: 'Ask Ubuntu', kind: 'qa' }),
      archive({ title: 'Wikipedia', kind: 'encyclopedia' }),
    ]);
    expect(groups.map(g => g.kind)).toEqual(['encyclopedia', 'qa', 'docs']);
  });

  it('sorts by title inside a group, not by filename', () => {
    // Filename order is exactly what made the old library look arbitrary.
    const groups = groupArchivesByKind([
      archive({ title: 'Zulip', kind: 'docs', filename: 'aaa.zim' }),
      archive({ title: 'Astro', kind: 'docs', filename: 'zzz.zim' }),
    ]);
    expect(groups[0].archives.map(a => a.title)).toEqual(['Astro', 'Zulip']);
  });

  it('buckets an unrecognised kind under other rather than dropping it', () => {
    const groups = groupArchivesByKind([
      archive({ kind: 'nonsense' as KnowledgeArchive['kind'] }),
    ]);
    expect(groups.map(g => g.kind)).toEqual(['other']);
  });
});

describe('searchCoverage', () => {
  it('stays quiet when everything was searched', () => {
    expect(searchCoverage({ searched: 4, skipped: 0 })).toBeNull();
  });

  it('speaks up when archives were left out', () => {
    // A search that quietly consulted 3 of 600 archives is the failure this
    // whole federation exists to make visible.
    expect(searchCoverage({ searched: 3, skipped: 12 })).toContain(
      '12 archives skipped'
    );
    expect(searchCoverage({ searched: 3, skipped: 1 })).toContain(
      '1 archive skipped'
    );
  });
});
