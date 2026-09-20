import type { KnowledgeDownload, KnowledgeDownloadStatus } from '../hooks/api';
import { sizeLabel } from './knowledge';

/** Downloads worth showing in the active strip, newest first. */
export const ACTIVE_STATUSES: KnowledgeDownloadStatus[] = [
  'queued',
  'downloading',
  'verifying',
  'paused',
  'error',
];

export function isActive(download: KnowledgeDownload): boolean {
  return ACTIVE_STATUSES.includes(download.status);
}

/**
 * How far along, 0–100, or null when the total is not yet known.
 *
 * Null rather than 0 on purpose: a progress bar pinned at zero says "stuck",
 * where an absent bar says "we don't know yet", and those are different.
 */
export function percent(download: KnowledgeDownload): number | null {
  if (!download.totalBytes) return null;
  const ratio = download.downloadedBytes / download.totalBytes;
  return Math.max(0, Math.min(100, Math.round(ratio * 1000) / 10));
}

export function rateLabel(bytesPerSecond?: number | null): string | null {
  if (!bytesPerSecond || bytesPerSecond <= 0) return null;
  return `${sizeLabel(bytesPerSecond)}/s`;
}

/**
 * Time remaining, in the coarsest unit that is still honest.
 *
 * A 107 GB archive is hours, so seconds are noise; below a minute the number
 * of seconds is the only useful thing left to say.
 */
export function etaLabel(download: KnowledgeDownload): string | null {
  const rate = download.bytesPerSecond;
  if (!rate || rate <= 0 || !download.totalBytes) return null;
  const remaining = download.totalBytes - download.downloadedBytes;
  if (remaining <= 0) return null;
  const seconds = Math.round(remaining / rate);
  if (seconds < 60) return `${seconds}s left`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min left`;
  const hours = seconds / 3600;
  return `${hours < 10 ? hours.toFixed(1) : Math.round(hours)} h left`;
}

const STATUS_LABELS: Record<KnowledgeDownloadStatus, string> = {
  queued: 'Queued',
  downloading: 'Downloading',
  // Its own status because a transfer assembled across two sessions cannot
  // carry an incremental hash — the whole file is read back once it is whole,
  // and on 107 GB that is long enough to need saying out loud.
  verifying: 'Checking the file',
  paused: 'Paused',
  done: 'Installed',
  error: 'Failed',
};

export function statusLabel(status: KnowledgeDownloadStatus): string {
  return STATUS_LABELS[status] ?? status;
}

/** The byte counter under the bar, e.g. `2.1 GB of 6.9 GB`. */
export function progressLabel(download: KnowledgeDownload): string {
  const done = sizeLabel(download.downloadedBytes);
  return download.totalBytes
    ? `${done} of ${sizeLabel(download.totalBytes)}`
    : done;
}

/** Which buttons a row should offer, given what it is doing. */
export function actionsFor(download: KnowledgeDownload): {
  pause: boolean;
  resume: boolean;
  remove: boolean;
} {
  return {
    pause: ['queued', 'downloading', 'verifying'].includes(download.status),
    // A failed download is resumable, not just dismissable: the `.part` is
    // kept precisely so a checksum failure or a dead mirror does not cost the
    // bytes already fetched.
    resume: ['paused', 'error'].includes(download.status),
    remove: download.status !== 'downloading',
  };
}
