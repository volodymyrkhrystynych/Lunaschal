import { describe, expect, it } from 'vitest';
import type { BackupHealth, BackupStatus } from '../hooks/api';
import {
  describeAge,
  describeLastRun,
  describeSnapshots,
  headline,
  shouldAutoExpand,
  usedFraction,
} from './backup';

function status(overrides: {
  health?: BackupHealth;
  ageDays?: number | null;
  count?: number;
  expectedCount?: number;
  latest?: string | null;
  freeBytes?: number | null;
  totalBytes?: number | null;
  timer?: Partial<BackupStatus['timer']>;
}): BackupStatus {
  return {
    configured: true,
    destination: '/media/expansion/lunaschal',
    mountPoint: '/media/expansion',
    configSource: 'settings',
    retentionDays: 14,
    health: overrides.health ?? 'ok',
    problems: [],
    mount: {
      present: true,
      writable: true,
      readonly: false,
      freeBytes: overrides.freeBytes ?? null,
      totalBytes: overrides.totalBytes ?? null,
    },
    snapshots: {
      latest: overrides.latest ?? '2026-08-22',
      ageDays: overrides.ageDays === undefined ? 0 : overrides.ageDays,
      count: overrides.count ?? 14,
      expectedCount: overrides.expectedCount ?? 14,
      latestSizeBytes: 1024,
      dates: [],
    },
    media: { lastModified: null },
    timer: {
      available: true,
      enabled: true,
      lastRun: null,
      lastResult: null,
      nextRun: null,
      ...overrides.timer,
    },
    run: {
      running: false,
      startedAt: null,
      finishedAt: null,
      ok: null,
      output: null,
    },
  };
}

describe('headline', () => {
  it('calls a fresh backup healthy', () => {
    const verdict = headline(status({ health: 'ok', ageDays: 0 }));
    expect(verdict.tone).toBe('good');
    expect(verdict.label).toBe('Healthy');
  });

  it('treats a missing drive as bad, not a soft warning', () => {
    // An unplugged drive is routine, but indistinguishable from a destination
    // that moved and will never come back — which is what happened here. The
    // ambiguous case must not read as fine.
    expect(headline(status({ health: 'unreachable' })).tone).toBe('bad');
  });

  it('reports staleness with the actual age', () => {
    const verdict = headline(status({ health: 'stale', ageDays: 19 }));
    expect(verdict.tone).toBe('bad');
    expect(verdict.detail).toContain('19 days');
  });

  it('treats a read-only drive as bad', () => {
    expect(headline(status({ health: 'readonly' })).tone).toBe('bad');
  });

  it('distinguishes an unwritable drive from a read-only one', () => {
    // Same symptom (nothing can be written), unrelated fixes: fsck vs. uid= in
    // fstab. The labels must not be interchangeable.
    const perms = headline(status({ health: 'permissions' }));
    const ro = headline(status({ health: 'readonly' }));
    expect(perms.tone).toBe('bad');
    expect(perms.label).not.toBe(ro.label);
    expect(perms.detail).toContain('read-write');
  });

  it('treats an empty but working drive as a warning only', () => {
    expect(headline(status({ health: 'empty', ageDays: null })).tone).toBe(
      'warn'
    );
  });

  it('treats no configuration as a warning', () => {
    expect(headline(status({ health: 'unconfigured' })).tone).toBe('warn');
  });
});

describe('describeAge', () => {
  it('uses words for the recent cases and a count beyond that', () => {
    expect(describeAge(0)).toBe('Backed up today.');
    expect(describeAge(1)).toBe('Backed up yesterday.');
    expect(describeAge(19)).toBe('Backed up 19 days ago.');
    expect(describeAge(null)).toBe('Never backed up.');
  });
});

describe('shouldAutoExpand', () => {
  it('opens the section only when the backup is actually broken', () => {
    expect(shouldAutoExpand(status({ health: 'stale', ageDays: 19 }))).toBe(
      true
    );
    expect(shouldAutoExpand(status({ health: 'unreachable' }))).toBe(true);
    expect(shouldAutoExpand(status({ health: 'readonly' }))).toBe(true);
    expect(shouldAutoExpand(status({ health: 'permissions' }))).toBe(true);
  });

  it('leaves a healthy or merely-empty backup collapsed', () => {
    // Collapsing every group by default is worthless if sections find reasons
    // to reopen themselves.
    expect(shouldAutoExpand(status({ health: 'ok' }))).toBe(false);
    expect(shouldAutoExpand(status({ health: 'empty' }))).toBe(false);
    expect(shouldAutoExpand(status({ health: 'unconfigured' }))).toBe(false);
  });

  it('stays collapsed before the status has loaded', () => {
    expect(shouldAutoExpand(undefined)).toBe(false);
  });
});

describe('usedFraction', () => {
  it('computes the used share of the drive', () => {
    expect(
      usedFraction(status({ freeBytes: 25, totalBytes: 100 }))
    ).toBeCloseTo(0.75);
  });

  it('returns null when the drive could not be measured', () => {
    expect(
      usedFraction(status({ freeBytes: null, totalBytes: null }))
    ).toBeNull();
    expect(usedFraction(status({ freeBytes: 10, totalBytes: 0 }))).toBeNull();
  });

  it('clamps a nonsensical free figure into range', () => {
    expect(usedFraction(status({ freeBytes: 200, totalBytes: 100 }))).toBe(0);
  });
});

describe('describeSnapshots', () => {
  it('does not flag a young backup as missing snapshots', () => {
    // Three days into a 14-day window, three snapshots is complete.
    expect(describeSnapshots(status({ count: 3, expectedCount: 3 }))).toBe(
      '3 kept'
    );
  });

  it('calls out a genuinely short count', () => {
    expect(describeSnapshots(status({ count: 3, expectedCount: 14 }))).toBe(
      '3 of 14 expected'
    );
  });

  it('says none for an empty drive', () => {
    expect(describeSnapshots(status({ count: 0, expectedCount: 0 }))).toBe(
      'none'
    );
  });
});

describe('describeLastRun', () => {
  it('refuses to call a run successful when nothing reached the drive', () => {
    // The exact deception this panel was built to end: nineteen consecutive
    // Result=success runs, none of which wrote a byte.
    const text = describeLastRun(
      status({
        health: 'unreachable',
        timer: { lastRun: '2026-08-22T03:00:10', lastResult: 'success' },
      })
    );
    expect(text).toContain('wrote nothing to the drive');
  });

  it('reports a genuinely successful run plainly', () => {
    const text = describeLastRun(
      status({
        health: 'ok',
        timer: { lastRun: '2026-08-22T03:00:10', lastResult: 'success' },
      })
    );
    expect(text).toContain('succeeded');
  });

  it('reports a failed run with its result', () => {
    const text = describeLastRun(
      status({
        health: 'stale',
        ageDays: 5,
        timer: { lastRun: '2026-08-22T03:00:10', lastResult: 'exit-code' },
      })
    );
    expect(text).toContain('failed (exit-code)');
  });

  it('says nothing when systemd could not be queried', () => {
    expect(
      describeLastRun(status({ timer: { available: false, lastRun: null } }))
    ).toBeNull();
  });
});
