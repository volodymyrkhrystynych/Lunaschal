/**
 * Presentation logic for Settings → Backup.
 *
 * Kept out of the component so it can be tested in the node environment, same
 * reason as the rest of src/lib. The backend decides *what* the health is
 * (backend/ops/backup_status.py); this decides how loudly to say it.
 */

import type { BackupHealth, BackupStatus } from '../hooks/api';

export type BackupTone = 'good' | 'warn' | 'bad';

export interface BackupHeadline {
  tone: BackupTone;
  /** Short status word for the badge. */
  label: string;
  /** One sentence under the badge. */
  detail: string;
}

/**
 * The one-line verdict at the top of the panel.
 *
 * `unreachable` is 'bad' rather than 'warn' even though an unplugged drive is
 * routine: this panel exists because a permanently-missing destination and a
 * briefly-missing one are indistinguishable from the job's exit code, and the
 * whole point is to stop treating the ambiguous case as fine. The detail text
 * carries the "if it's just unplugged, ignore this" nuance instead.
 */
export function headline(status: BackupStatus): BackupHeadline {
  switch (status.health) {
    case 'ok':
      return {
        tone: 'good',
        label: 'Healthy',
        detail: describeAge(status.snapshots.ageDays),
      };
    case 'stale':
      return {
        tone: 'bad',
        label: 'Stale',
        detail: `No backup has landed in ${status.snapshots.ageDays} days.`,
      };
    case 'unreachable':
      return {
        tone: 'bad',
        label: 'Drive not found',
        detail: 'The destination is missing — unplugged, or the path is wrong.',
      };
    case 'readonly':
      return {
        tone: 'bad',
        label: 'Read-only',
        detail: 'The drive is mounted read-only; nothing can be written to it.',
      };
    case 'permissions':
      return {
        tone: 'bad',
        label: 'Not writable',
        detail:
          'The drive is mounted read-write, but this user cannot write to it.',
      };
    case 'empty':
      return {
        tone: 'warn',
        label: 'No snapshots yet',
        detail: 'The drive is ready but holds no backup yet.',
      };
    case 'unconfigured':
      return {
        tone: 'warn',
        label: 'Not configured',
        detail: 'No backup destination is set.',
      };
  }
}

/** "Backed up today" / "…yesterday" / "…3 days ago". */
export function describeAge(ageDays: number | null): string {
  if (ageDays == null) return 'Never backed up.';
  if (ageDays === 0) return 'Backed up today.';
  if (ageDays === 1) return 'Backed up yesterday.';
  return `Backed up ${ageDays} days ago.`;
}

/**
 * Whether the health is bad enough to pull the whole Settings section open on
 * its own.
 *
 * Every group is collapsed by default now, and a backup that has quietly died
 * is exactly the thing a collapsed panel would keep hiding — the failure this
 * feature was built for went unnoticed for nineteen days. A healthy backup
 * stays folded away like everything else.
 */
export function shouldAutoExpand(status: BackupStatus | undefined): boolean {
  if (!status) return false;
  return headline(status).tone === 'bad';
}

/** Free-space share of the destination, or null when it could not be read. */
export function usedFraction(status: BackupStatus): number | null {
  const { freeBytes, totalBytes } = status.mount;
  if (freeBytes == null || totalBytes == null || totalBytes <= 0) return null;
  return Math.min(1, Math.max(0, (totalBytes - freeBytes) / totalBytes));
}

/**
 * "14 of 14 kept" — but only when the number is meaningful.
 *
 * A drive three days into its retention window holds three snapshots and is
 * perfectly healthy, so the comparison is against `expectedCount` (what should
 * be there given how long it has been running), never against the raw
 * retention setting.
 */
export function describeSnapshots(status: BackupStatus): string {
  const { count, expectedCount } = status.snapshots;
  if (count === 0) return 'none';
  if (count < expectedCount) {
    return `${count} of ${expectedCount} expected`;
  }
  return `${count} kept`;
}

/**
 * How the last automated run ended, phrased so a misleading "success" cannot
 * pass unchallenged.
 *
 * systemd's `Result=success` is exactly what the dead backup reported every
 * night for nineteen days, so a successful exit is only reported as such when
 * the drive actually corroborates it.
 */
export function describeLastRun(status: BackupStatus): string | null {
  const { lastRun, lastResult, available } = status.timer;
  if (!available || !lastRun) return null;
  const when = new Date(lastRun).toLocaleString();
  if (lastResult !== 'success')
    return `Last run ${when} — failed (${lastResult}).`;
  if (headline(status).tone === 'bad') {
    return `Last run ${when} — exited cleanly, but wrote nothing to the drive.`;
  }
  return `Last run ${when} — succeeded.`;
}

/** Badge colours, matching the red/green dot convention used elsewhere. */
export const TONE_CLASSES: Record<BackupTone, string> = {
  good: 'text-green-400',
  warn: 'text-amber-400',
  bad: 'text-red-400',
};

export const TONE_DOT: Record<BackupTone, string> = {
  good: 'bg-green-400',
  warn: 'bg-amber-400',
  bad: 'bg-red-400',
};

export type { BackupHealth };
