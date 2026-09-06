import { describe, expect, it } from 'vitest';
import {
  anyLive,
  daysUntilPurge,
  filterTorrents,
  formatBytes,
  formatEta,
  formatPercent,
  formatSpeed,
  sortTorrents,
  stateColor,
  stateLabel,
  vpnSummary,
  type Torrent,
  type VpnStatus,
} from './torrents';

function torrent(over: Partial<Torrent> = {}): Torrent {
  return {
    id: 'ULID',
    infoHash: 'aabb',
    name: 'Debian ISO',
    state: 'downloading',
    stateGroup: 'downloading',
    live: true,
    progress: 0.5,
    size: 1024,
    downloaded: 512,
    uploaded: 0,
    ratio: 0,
    dlSpeed: 1024,
    upSpeed: 0,
    eta: 120,
    numSeeds: 3,
    numLeechs: 1,
    category: '',
    savePath: '/downloads',
    contentPath: '/downloads/Debian ISO',
    addedAt: 1000,
    completedAt: null,
    ratioLimit: null,
    seedingMinutes: null,
    dlLimit: 0,
    upLimit: 0,
    note: null,
    retentionDays: null,
    tracked: true,
    ...over,
  };
}

describe('formatting', () => {
  it('uses binary units, like every torrent client', () => {
    expect(formatBytes(1024)).toBe('1.0 KiB');
    expect(formatBytes(1024 * 1024 * 1536)).toBe('1.5 GiB');
  });

  it('does not put a decimal on plain bytes', () => {
    expect(formatBytes(512)).toBe('512 B');
  });

  it('treats zero and nonsense as zero rather than NaN', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(-5)).toBe('0 B');
    expect(formatBytes(Number.NaN)).toBe('0 B');
  });

  it('shows an em dash for no speed rather than "0 B/s"', () => {
    expect(formatSpeed(0)).toBe('—');
    expect(formatSpeed(2048)).toBe('2.0 KiB/s');
  });

  it('renders an unknown ETA as an em dash, never as 0s', () => {
    // The backend already turned qBittorrent's 8640000 sentinel into null.
    expect(formatEta(null)).toBe('—');
    expect(formatEta(0)).toBe('—');
  });

  it('scales the ETA to the largest useful unit', () => {
    expect(formatEta(45)).toBe('45s');
    expect(formatEta(90)).toBe('1m');
    expect(formatEta(3700)).toBe('1h 1m');
    expect(formatEta(90000)).toBe('1d 1h');
  });

  it('keeps one decimal of progress and never exceeds 100%', () => {
    expect(formatPercent(0.5)).toBe('50%');
    expect(formatPercent(0.1234)).toBe('12.3%');
    expect(formatPercent(1.0001)).toBe('100%');
  });
});

describe('state presentation', () => {
  it('labels every group the backend can emit', () => {
    for (const g of [
      'downloading',
      'seeding',
      'stalled',
      'paused',
      'complete',
      'checking',
      'queued',
      'error',
    ] as const) {
      expect(stateLabel(g)).not.toBe('Unknown');
      expect(stateColor(g)).toBeTruthy();
    }
  });

  it('colours a stalled torrent as a warning, not an error', () => {
    // Waiting for peers is normal and usually resolves itself.
    expect(stateColor('stalled')).not.toBe(stateColor('error'));
  });
});

describe('polling', () => {
  it('keeps polling while anything is still moving', () => {
    expect(anyLive([torrent({ live: false }), torrent({ live: true })])).toBe(
      true
    );
  });

  it('stops once everything has settled', () => {
    expect(anyLive([torrent({ live: false })])).toBe(false);
    expect(anyLive([])).toBe(false);
  });
});

describe('sorting and filtering', () => {
  const list = [
    torrent({
      infoHash: 'a',
      name: 'Beta',
      addedAt: 1,
      progress: 0.9,
      size: 10,
      ratio: 3,
    }),
    torrent({
      infoHash: 'b',
      name: 'Alpha',
      addedAt: 9,
      progress: 0.1,
      size: 99,
      ratio: 1,
    }),
  ];

  it('defaults to newest first', () => {
    expect(sortTorrents(list, 'added').map(t => t.infoHash)).toEqual([
      'b',
      'a',
    ]);
  });

  it('sorts by name naturally', () => {
    expect(sortTorrents(list, 'name').map(t => t.name)).toEqual([
      'Alpha',
      'Beta',
    ]);
  });

  it('sorts least-complete first, so what still needs attention is on top', () => {
    expect(sortTorrents(list, 'progress').map(t => t.infoHash)).toEqual([
      'b',
      'a',
    ]);
  });

  it('does not mutate the input', () => {
    const before = list.map(t => t.infoHash);
    sortTorrents(list, 'name');
    expect(list.map(t => t.infoHash)).toEqual(before);
  });

  it('filters by name, case-insensitively', () => {
    expect(filterTorrents(list, { search: 'alph' }).map(t => t.name)).toEqual([
      'Alpha',
    ]);
  });

  it('combines filters', () => {
    const tagged = [
      torrent({ name: 'Alpha', category: 'linux', stateGroup: 'seeding' }),
    ];
    expect(
      filterTorrents(tagged, { category: 'linux', group: 'seeding' })
    ).toHaveLength(1);
    expect(
      filterTorrents(tagged, { category: 'linux', group: 'error' })
    ).toHaveLength(0);
  });

  it('an empty search matches everything', () => {
    expect(filterTorrents(list, { search: '  ' })).toHaveLength(2);
  });
});

describe('retention countdown', () => {
  const now = Date.UTC(2026, 8, 5) as number;

  it('is absent when the torrent is kept forever, which is the default', () => {
    expect(daysUntilPurge(torrent({ retentionDays: null }), now)).toBeNull();
    expect(daysUntilPurge(torrent({ retentionDays: 0 }), now)).toBeNull();
  });

  it('does not count down before the torrent has finished', () => {
    // Measured from completion, matching backend/torrent/retention.py.
    expect(
      daysUntilPurge(torrent({ retentionDays: 7, completedAt: null }), now)
    ).toBeNull();
  });

  it('counts whole days from completion', () => {
    const completedAt = now / 1000 - 2 * 86400;
    expect(
      daysUntilPurge(torrent({ retentionDays: 7, completedAt }), now)
    ).toBe(5);
  });

  it('never goes negative once overdue', () => {
    const completedAt = now / 1000 - 30 * 86400;
    expect(
      daysUntilPurge(torrent({ retentionDays: 7, completedAt }), now)
    ).toBe(0);
  });
});

describe('vpn banner', () => {
  function vpn(over: Partial<VpnStatus> = {}): VpnStatus {
    return {
      available: true,
      connected: true,
      status: 'running',
      ip: '185.111.110.66',
      country: 'Canada',
      city: 'Toronto',
      forwardedPort: 51413,
      ...over,
    };
  }

  it('shows the exit address, which is the question the feature exists to answer', () => {
    const s = vpnSummary(vpn());
    expect(s.tone).toBe('ok');
    expect(s.headline).toContain('185.111.110.66');
    expect(s.headline).toContain('Toronto, Canada');
  });

  it('a missing stack tells you how to start it', () => {
    const s = vpnSummary(vpn({ available: false }));
    expect(s.tone).toBe('bad');
    expect(s.detail).toContain('systemctl --user start lunaschal-torrent');
  });

  it('distinguishes a stopped stack from a down tunnel', () => {
    // Different fixes, so they must not read the same.
    expect(vpnSummary(vpn({ available: false })).headline).not.toBe(
      vpnSummary(vpn({ connected: false, status: 'stopped' })).headline
    );
  });

  it('never reads as reassuring when it does not know', () => {
    expect(vpnSummary(undefined).tone).toBe('bad');
  });

  it('no forwarded port is a caveat, not a failure', () => {
    const s = vpnSummary(vpn({ forwardedPort: null }));
    expect(s.tone).toBe('warn');
    expect(s.detail).toContain('seeding will be slow');
  });
});
