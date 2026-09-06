// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Torrent as TorrentView } from './TorrentList';
import { ShortcutProvider } from '../../shortcuts/ShortcutProvider';
import type { Torrent, VpnStatus } from '../../lib/torrents';

const { torrent, vpnUp } = vi.hoisted(() => {
  const torrent = (over: Partial<Torrent> = {}): Torrent => ({
    id: 'ULID',
    infoHash: 'aabb',
    name: 'Debian ISO',
    state: 'downloading',
    stateGroup: 'downloading',
    live: true,
    progress: 0.42,
    size: 1024 * 1024,
    downloaded: 512,
    uploaded: 0,
    ratio: 0,
    dlSpeed: 2048,
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
  });
  const vpnUp: VpnStatus = {
    available: true,
    connected: true,
    status: 'running',
    ip: '185.111.110.66',
    country: 'Canada',
    city: 'Toronto',
    forwardedPort: 51413,
  };
  return { torrent, vpnUp };
});

const list = vi.fn();
const pause = vi.fn();
const resume = vi.fn();
const remove = vi.fn();

vi.mock('../../hooks/api', () => ({
  api: {
    torrents: {
      list: (...a: unknown[]) => list(...a),
      pause: (...a: unknown[]) => pause(...a),
      resume: (...a: unknown[]) => resume(...a),
      recheck: vi.fn(),
      remove: (...a: unknown[]) => remove(...a),
      categories: vi.fn().mockResolvedValue(['linux']),
      files: vi.fn().mockResolvedValue([]),
      fileUrl: (h: string, i: number) =>
        `/api/torrents/${h}/files/${i}/download`,
      update: vi.fn().mockResolvedValue({ ok: true }),
      status: vi.fn(),
      add: vi.fn(),
      addFiles: vi.fn(),
    },
    shortcuts: { get: vi.fn().mockResolvedValue({ bindings: {} }) },
    settings: { get: vi.fn().mockResolvedValue({}) },
  },
}));

function renderView() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ShortcutProvider
        currentView="torrent"
        onViewChange={() => {}}
        onToggleSidebar={() => {}}
      >
        <TorrentView />
      </ShortcutProvider>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  pause.mockResolvedValue({ ok: true });
  resume.mockResolvedValue({ ok: true });
  remove.mockResolvedValue({ ok: true });
});

describe('the VPN banner', () => {
  it('shows the exit address, which is the question the tab exists to answer', async () => {
    list.mockResolvedValue({ torrents: [torrent()], vpn: vpnUp });
    renderView();
    expect(await screen.findByText(/185\.111\.110\.66/)).toBeTruthy();
    expect(screen.getByText(/Toronto, Canada/)).toBeTruthy();
    expect(screen.getByText(/Port 51413 forwarded/)).toBeTruthy();
  });

  it('raises an alert when the tunnel is down', async () => {
    list.mockResolvedValue({
      torrents: [],
      vpn: { ...vpnUp, connected: false, status: 'stopped' },
    });
    renderView();
    expect(await screen.findByText(/ProtonVPN tunnel is down/)).toBeTruthy();
    expect(screen.getByRole('alert')).toBeTruthy();
  });

  it('tells you how to start a stack that is not running', async () => {
    list.mockResolvedValue({
      torrents: [],
      vpn: { ...vpnUp, available: false },
    });
    renderView();
    expect(
      await screen.findByText(/systemctl --user start lunaschal-torrent/)
    ).toBeTruthy();
  });
});

describe('the list', () => {
  it('renders progress, speed and peers for a torrent', async () => {
    list.mockResolvedValue({ torrents: [torrent()], vpn: vpnUp });
    renderView();
    // The name shows twice: once in the row, once in the detail pane.
    expect(await screen.findAllByText('Debian ISO')).toHaveLength(2);
    expect(screen.getByText('42%')).toBeTruthy();
    expect(screen.getByText('↓ 2.0 KiB/s')).toBeTruthy();
    expect(screen.getByText('3/1 peers')).toBeTruthy();
  });

  it('says so plainly when there is nothing to show', async () => {
    list.mockResolvedValue({ torrents: [], vpn: vpnUp });
    renderView();
    expect(
      await screen.findByText(/Paste a magnet link to start/)
    ).toBeTruthy();
  });

  it('surfaces a stopped stack instead of spinning forever', async () => {
    list.mockRejectedValue(new Error('The torrent stack is not reachable.'));
    renderView();
    expect(await screen.findByText(/not reachable/)).toBeTruthy();
  });

  it('filters by name', async () => {
    list.mockResolvedValue({
      torrents: [torrent(), torrent({ infoHash: 'ccdd', name: 'Ubuntu ISO' })],
      vpn: vpnUp,
    });
    renderView();
    await screen.findAllByText('Debian ISO');
    fireEvent.change(screen.getByPlaceholderText('Filter by name'), {
      target: { value: 'ubuntu' },
    });
    expect(screen.queryAllByText('Debian ISO')).toHaveLength(0);
    expect(screen.getAllByText('Ubuntu ISO').length).toBeGreaterThan(0);
  });
});

describe('controls', () => {
  it('pauses a running torrent', async () => {
    list.mockResolvedValue({ torrents: [torrent()], vpn: vpnUp });
    renderView();
    fireEvent.click(await screen.findByText('Pause'));
    await waitFor(() => expect(pause).toHaveBeenCalledWith('aabb'));
  });

  it('offers Resume, not Pause, for a paused one', async () => {
    list.mockResolvedValue({
      torrents: [
        torrent({ stateGroup: 'paused', state: 'pausedDL', live: false }),
      ],
      vpn: vpnUp,
    });
    renderView();
    fireEvent.click(await screen.findByText('Resume'));
    await waitFor(() => expect(resume).toHaveBeenCalledWith('aabb'));
  });

  it('removes without touching the files by default', async () => {
    list.mockResolvedValue({ torrents: [torrent()], vpn: vpnUp });
    renderView();
    fireEvent.click(await screen.findByText('Remove'));
    await waitFor(() => expect(remove).toHaveBeenCalledWith('aabb', false));
  });

  it('asks before deleting the files, since that cannot be undone', async () => {
    list.mockResolvedValue({ torrents: [torrent()], vpn: vpnUp });
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderView();
    fireEvent.click(await screen.findByText('Delete + files'));
    expect(confirm).toHaveBeenCalled();
    expect(remove).not.toHaveBeenCalled();

    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByText('Delete + files'));
    await waitFor(() => expect(remove).toHaveBeenCalledWith('aabb', true));
    confirm.mockRestore();
  });
});
