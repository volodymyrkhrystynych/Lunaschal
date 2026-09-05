// Pure presentation logic for the Torrent tab, kept out of the components so it
// runs under Vitest's default node environment.
//
// Nothing here decides *what* a torrent's state is — the backend already
// grouped it (backend/torrent/merge.py's state_group), because both the list and
// the sidebar badge need the same answer and two copies of that mapping would
// drift. This file turns that answer into words, colours and units.

export type TorrentStateGroup =
  | 'downloading'
  | 'seeding'
  | 'stalled'
  | 'paused'
  | 'complete'
  | 'checking'
  | 'queued'
  | 'error';

export interface Torrent {
  id: string | null;
  infoHash: string;
  name: string;
  state: string;
  stateGroup: TorrentStateGroup;
  live: boolean;
  progress: number;
  size: number;
  downloaded: number;
  uploaded: number;
  ratio: number;
  dlSpeed: number;
  upSpeed: number;
  eta: number | null;
  numSeeds: number;
  numLeechs: number;
  category: string;
  savePath: string;
  contentPath: string;
  addedAt: number | null;
  completedAt: number | null;
  ratioLimit: number | null;
  seedingMinutes: number | null;
  dlLimit: number;
  upLimit: number;
  note: string | null;
  retentionDays: number | null;
  tracked: boolean;
}

const LABELS: Record<TorrentStateGroup, string> = {
  downloading: 'Downloading',
  seeding: 'Seeding',
  stalled: 'Stalled',
  paused: 'Paused',
  complete: 'Done',
  checking: 'Checking',
  queued: 'Queued',
  error: 'Error',
};

// Tailwind text colours, matching the palette the rest of the app uses for
// status. 'stalled' is amber rather than red on purpose: a stalled torrent is
// waiting for peers, which is normal and usually resolves itself.
const COLORS: Record<TorrentStateGroup, string> = {
  downloading: 'text-blue-400',
  seeding: 'text-green-400',
  stalled: 'text-yellow-400',
  paused: 'text-[var(--color-text-muted)]',
  complete: 'text-green-400',
  checking: 'text-blue-400',
  queued: 'text-[var(--color-text-muted)]',
  error: 'text-red-400',
};

export function stateLabel(group: TorrentStateGroup): string {
  return LABELS[group] ?? 'Unknown';
}

export function stateColor(group: TorrentStateGroup): string {
  return COLORS[group] ?? 'text-red-400';
}

/** Binary units, because that is what every torrent client reports in. */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  const exp = Math.min(
    units.length - 1,
    Math.floor(Math.log(bytes) / Math.log(1024))
  );
  const value = bytes / 1024 ** exp;
  // No decimal on plain bytes — "512.0 B" reads as a rounding artefact.
  return `${exp === 0 ? value : value.toFixed(value < 10 ? 1 : 0)} ${units[exp]}`;
}

export function formatSpeed(bytesPerSecond: number): string {
  if (!bytesPerSecond) return '—';
  return `${formatBytes(bytesPerSecond)}/s`;
}

/** null means the client has no estimate — never render that as "0s" or "100 days". */
export function formatEta(seconds: number | null): string {
  if (seconds === null || seconds <= 0) return '—';
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m`;
  return `${seconds}s`;
}

export function formatPercent(progress: number): string {
  return `${Math.min(100, Math.round(progress * 1000) / 10)}%`;
}

/** Whether anything on screen is still moving, i.e. whether to keep polling. */
export function anyLive(torrents: Torrent[]): boolean {
  return torrents.some(t => t.live);
}

export const SORTS = ['added', 'name', 'progress', 'size', 'ratio'] as const;
export type TorrentSort = (typeof SORTS)[number];

export function sortTorrents(
  torrents: Torrent[],
  sort: TorrentSort
): Torrent[] {
  const copy = [...torrents];
  switch (sort) {
    case 'name':
      return copy.sort((a, b) =>
        a.name.localeCompare(b.name, undefined, { numeric: true })
      );
    case 'progress':
      return copy.sort((a, b) => a.progress - b.progress);
    case 'size':
      return copy.sort((a, b) => b.size - a.size);
    case 'ratio':
      return copy.sort((a, b) => b.ratio - a.ratio);
    case 'added':
    default:
      return copy.sort((a, b) => (b.addedAt ?? 0) - (a.addedAt ?? 0));
  }
}

export function filterTorrents(
  torrents: Torrent[],
  {
    search,
    category,
    group,
  }: { search?: string; category?: string; group?: TorrentStateGroup }
): Torrent[] {
  const needle = (search ?? '').trim().toLowerCase();
  return torrents.filter(t => {
    if (needle && !t.name.toLowerCase().includes(needle)) return false;
    if (category && t.category !== category) return false;
    if (group && t.stateGroup !== group) return false;
    return true;
  });
}

/**
 * How long until this torrent is deleted, in whole days.
 *
 * Counted from completion rather than from when it was added, matching
 * backend/torrent/retention.py — a download that took a week has not been
 * *kept* for a week. null means it is never deleted, which is the default.
 */
export function daysUntilPurge(torrent: Torrent, nowMs: number): number | null {
  if (!torrent.retentionDays || torrent.retentionDays <= 0) return null;
  if (!torrent.completedAt) return null;
  const dueMs = (torrent.completedAt + torrent.retentionDays * 86400) * 1000;
  return Math.max(0, Math.ceil((dueMs - nowMs) / 86400000));
}

export interface VpnStatus {
  available: boolean;
  connected: boolean;
  status: string;
  ip: string | null;
  country: string | null;
  city: string | null;
  forwardedPort: number | null;
}

/**
 * What the banner says. Three states rather than two, because "gluetun is not
 * running" and "gluetun is running but the tunnel is down" have different
 * fixes, and neither should read like the reassuring case.
 */
export function vpnSummary(vpn: VpnStatus | undefined): {
  tone: 'ok' | 'warn' | 'bad';
  headline: string;
  detail: string;
} {
  if (!vpn || !vpn.available) {
    return {
      tone: 'bad',
      headline: 'Torrent stack is not running',
      detail: 'systemctl --user start lunaschal-torrent',
    };
  }
  if (!vpn.connected) {
    return {
      tone: 'bad',
      headline: 'ProtonVPN tunnel is down',
      detail: `gluetun reports "${vpn.status}" — nothing can reach a tracker until it reconnects.`,
    };
  }
  const where = [vpn.city, vpn.country].filter(Boolean).join(', ');
  return {
    // No forwarded port still works, it just seeds badly — a caveat, not a failure.
    tone: vpn.forwardedPort ? 'ok' : 'warn',
    headline: `Exiting via ${vpn.ip ?? 'ProtonVPN'}${where ? ` (${where})` : ''}`,
    detail: vpn.forwardedPort
      ? `Port ${vpn.forwardedPort} forwarded`
      : 'No forwarded port yet — seeding will be slow until NAT-PMP grants one.',
  };
}
