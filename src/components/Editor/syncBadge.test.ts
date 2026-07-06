import { describe, it, expect } from 'vitest';
import { syncBadge, aheadBehind, relativeTime } from './syncBadge';
import type { FileSyncStatus } from '../../hooks/api';

const base: FileSyncStatus = {
  isRepo: true, hasRemote: true, remoteUrl: 'git@nas:notes.git', branch: 'main',
  dirty: false, ahead: 0, behind: 0, hasUpstream: true, conflicted: false,
  detached: false, running: false, phase: 'idle', error: null, lastSync: null,
};

describe('relativeTime', () => {
  const now = 1_000_000_000_000; // fixed "now" in ms
  it('handles never / just now / minutes / hours / days', () => {
    expect(relativeTime(null, now)).toBe('never');
    expect(relativeTime(now / 1000 - 10, now)).toBe('just now');
    expect(relativeTime(now / 1000 - 120, now)).toBe('2m ago');
    expect(relativeTime(now / 1000 - 7200, now)).toBe('2h ago');
    expect(relativeTime(now / 1000 - 172800, now)).toBe('2d ago');
  });
});

describe('aheadBehind', () => {
  it('formats ↑/↓ and omits zeros', () => {
    expect(aheadBehind({ ahead: 0, behind: 0 })).toBe('');
    expect(aheadBehind({ ahead: 2, behind: 0 })).toBe('↑2');
    expect(aheadBehind({ ahead: 0, behind: 1 })).toBe('↓1');
    expect(aheadBehind({ ahead: 2, behind: 1 })).toBe('↑2 ↓1');
  });
});

describe('syncBadge', () => {
  it('is disabled and informative when not configured', () => {
    expect(syncBadge(undefined).canSync).toBe(false);
    expect(syncBadge({ ...base, isRepo: false }).label).toBe('Not set up');
    expect(syncBadge({ ...base, hasRemote: false }).label).toBe('No remote');
  });

  it('shows busy state while running', () => {
    const b = syncBadge({ ...base, running: true, phase: 'pull' });
    expect(b.tone).toBe('busy');
    expect(b.canSync).toBe(false);
    expect(b.label).toContain('pull');
  });

  it('surfaces conflict and error as syncable error states', () => {
    expect(syncBadge({ ...base, conflicted: true })).toMatchObject({ label: 'Conflict', canSync: true, tone: 'error' });
    expect(syncBadge({ ...base, error: 'boom' })).toMatchObject({ label: 'Sync failed', tone: 'error' });
  });

  it('shows pending work when dirty or ahead/behind', () => {
    expect(syncBadge({ ...base, ahead: 2, behind: 1 }).label).toBe('Sync ↑2 ↓1');
    expect(syncBadge({ ...base, dirty: true }).label).toBe('Sync •');
    expect(syncBadge({ ...base, dirty: true }).tone).toBe('pending');
  });

  it('shows last-sync time when clean and in sync', () => {
    const now = 1_000_000_000_000;
    const b = syncBadge({ ...base, lastSync: now / 1000 - 120 }, now);
    expect(b.label).toBe('Synced 2m ago');
    expect(b.tone).toBe('idle');
    expect(b.canSync).toBe(true);
  });
});
