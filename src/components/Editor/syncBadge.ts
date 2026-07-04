import type { FileSyncStatus } from '../../hooks/api';

export interface SyncBadge {
  /** Short label for the button/status line. */
  label: string;
  /** Whether the Sync action should be enabled. */
  canSync: boolean;
  /** Severity for styling. */
  tone: 'idle' | 'busy' | 'pending' | 'error';
}

export function relativeTime(unixSeconds: number | null, now: number = Date.now()): string {
  if (!unixSeconds) return 'never';
  const secs = Math.max(0, Math.floor(now / 1000 - unixSeconds));
  if (secs < 60) return 'just now';
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

/** Compact "↑2 ↓1" ahead/behind indicator, or '' when nothing pending. */
export function aheadBehind(status: Pick<FileSyncStatus, 'ahead' | 'behind'>): string {
  const parts: string[] = [];
  if (status.ahead) parts.push(`↑${status.ahead}`);
  if (status.behind) parts.push(`↓${status.behind}`);
  return parts.join(' ');
}

/** Derive the user-facing sync badge from raw status. Pure — unit tested. */
export function syncBadge(status: FileSyncStatus | undefined, now: number = Date.now()): SyncBadge {
  if (!status) return { label: '…', canSync: false, tone: 'idle' };
  if (!status.isRepo) return { label: 'Not set up', canSync: false, tone: 'idle' };
  if (!status.hasRemote) return { label: 'No remote', canSync: false, tone: 'idle' };
  if (status.running) return { label: `Syncing… ${status.phase}`, canSync: false, tone: 'busy' };
  if (status.conflicted) return { label: 'Conflict', canSync: true, tone: 'error' };
  if (status.error) return { label: 'Sync failed', canSync: true, tone: 'error' };

  const ab = aheadBehind(status);
  if (status.dirty || ab) {
    const bits = [ab, status.dirty ? '•' : ''].filter(Boolean).join(' ');
    return { label: `Sync ${bits}`.trim(), canSync: true, tone: 'pending' };
  }
  return { label: `Synced ${relativeTime(status.lastSync, now)}`, canSync: true, tone: 'idle' };
}
