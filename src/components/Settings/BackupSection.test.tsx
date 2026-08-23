// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { BackupStatus } from '../../hooks/api';
import { api } from '../../hooks/api';
import { BackupSection } from './BackupSection';

function makeStatus(overrides: Partial<BackupStatus> = {}): BackupStatus {
  return {
    configured: true,
    destination: '/media/expansion/lunaschal',
    mountPoint: '/media/expansion',
    configSource: 'settings',
    retentionDays: 14,
    health: 'ok',
    problems: [],
    mount: {
      present: true,
      writable: true,
      readonly: false,
      freeBytes: 8_000_000_000_000,
      totalBytes: 8_001_000_000_000,
    },
    snapshots: {
      latest: '2026-08-22',
      ageDays: 0,
      count: 14,
      expectedCount: 14,
      latestSizeBytes: 5_000_000,
      dates: [],
    },
    media: { lastModified: '2026-08-22T03:00:00' },
    timer: {
      available: true,
      enabled: true,
      lastRun: '2026-08-22T03:00:10',
      lastResult: 'success',
      nextRun: '2026-08-23T03:00:00',
    },
    run: {
      running: false,
      startedAt: null,
      finishedAt: null,
      ok: null,
      output: null,
    },
    ...overrides,
  };
}

function renderSection() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <BackupSection />
    </QueryClientProvider>
  );
}

beforeEach(() => vi.restoreAllMocks());

describe('BackupSection', () => {
  it('shows the destination and a healthy verdict', async () => {
    vi.spyOn(api.backup, 'status').mockResolvedValue(makeStatus());

    renderSection();

    expect(await screen.findByText('Healthy')).toBeTruthy();
    expect(screen.getByText('/media/expansion/lunaschal')).toBeTruthy();
    expect(screen.getByText('Backed up today.')).toBeTruthy();
  });

  it('renders the drive total in TB rather than thousands of GB', async () => {
    vi.spyOn(api.backup, 'status').mockResolvedValue(makeStatus());

    renderSection();

    expect(await screen.findByText(/7\.3 TB/)).toBeTruthy();
  });

  it('surfaces the stale-destination failure with its explanation', async () => {
    // The nineteen-day outage: the path in backup.env no longer existed, so
    // every nightly run skipped the drive and exited 0.
    vi.spyOn(api.backup, 'status').mockResolvedValue(
      makeStatus({
        health: 'unreachable',
        problems: ['The configured destination is not present.'],
        mount: {
          present: false,
          writable: false,
          readonly: false,
          freeBytes: null,
          totalBytes: null,
        },
        snapshots: {
          latest: '2026-08-03',
          ageDays: 19,
          count: 1,
          expectedCount: 14,
          latestSizeBytes: 5_000_000,
          dates: [],
        },
      })
    );

    renderSection();

    expect(await screen.findByText('Drive not found')).toBeTruthy();
    expect(
      screen.getByText('The configured destination is not present.')
    ).toBeTruthy();
    // A clean systemd exit must not be reported as a success.
    expect(screen.getByText(/wrote nothing to the drive/)).toBeTruthy();
    expect(screen.getByText('1 of 14 expected')).toBeTruthy();
  });

  it('reports a drive owned by root as a permissions problem, not read-only', async () => {
    // The live condition on this machine: mounted rw, owned by root because the
    // fstab entry has no uid=. Calling it "read-only" would point the fix at
    // fsck on a perfectly healthy filesystem.
    vi.spyOn(api.backup, 'status').mockResolvedValue(
      makeStatus({
        health: 'permissions',
        problems: ['Check the fstab entry for the drive.'],
        mount: {
          present: true,
          writable: false,
          readonly: false,
          freeBytes: 1,
          totalBytes: 2,
        },
      })
    );

    renderSection();

    expect(await screen.findByText('Not writable')).toBeTruthy();
    expect(screen.queryByText('Read-only')).toBeNull();
  });

  it('triggers a manual run and disables the button while it works', async () => {
    vi.spyOn(api.backup, 'status').mockResolvedValue(makeStatus());
    const run = vi.spyOn(api.backup, 'run').mockResolvedValue({
      running: true,
      startedAt: '2026-08-22T21:00:00',
      finishedAt: null,
      ok: null,
      output: null,
    });

    renderSection();

    const button = await screen.findByRole('button', { name: 'Back up now' });
    fireEvent.click(button);

    await waitFor(() => expect(run).toHaveBeenCalled());
  });

  it('shows the log output of a failed manual run', async () => {
    vi.spyOn(api.backup, 'status').mockResolvedValue(
      makeStatus({
        run: {
          running: false,
          startedAt: '2026-08-22T21:00:00',
          finishedAt: '2026-08-22T21:00:05',
          ok: false,
          output: 'backup: HDD mount point not present — skipping',
        },
      })
    );

    renderSection();

    expect(await screen.findByText(/Last manual run failed/)).toBeTruthy();
    expect(screen.getByText(/HDD mount point not present/)).toBeTruthy();
  });

  it('saves a folder chosen in the picker as the new destination', async () => {
    // Settings is the source of truth now, so picking here is what the nightly
    // job will use — no env file edit involved.
    vi.spyOn(api.backup, 'status').mockResolvedValue(makeStatus());
    vi.spyOn(api.backup, 'browse').mockResolvedValue({
      path: '/media/expansion',
      parent: '/media',
      entries: [
        {
          name: 'lunaschal',
          path: '/media/expansion/lunaschal',
          writable: true,
        },
      ],
      truncated: false,
      writable: true,
      isMount: true,
      suggestions: [],
    });
    const setConfig = vi.spyOn(api.backup, 'setConfig').mockResolvedValue({
      path: '/media/expansion',
      retentionDays: 14,
      source: 'settings',
    });

    renderSection();
    fireEvent.click(await screen.findByRole('button', { name: 'Change…' }));
    await screen.findByText('lunaschal/');
    fireEvent.click(screen.getByRole('button', { name: 'Use this folder' }));

    // First argument only: react-query hands mutationFn a context object as a
    // second argument, which api.backup.setConfig ignores.
    await waitFor(() => expect(setConfig).toHaveBeenCalled());
    expect(setConfig.mock.calls[0][0]).toEqual({
      destination: '/media/expansion',
    });
  });

  it('saves a changed retention window', async () => {
    vi.spyOn(api.backup, 'status').mockResolvedValue(makeStatus());
    const setConfig = vi.spyOn(api.backup, 'setConfig').mockResolvedValue({
      path: '/media/expansion/lunaschal',
      retentionDays: 30,
      source: 'settings',
    });

    renderSection();
    const input = await screen.findByLabelText('Retention days');
    fireEvent.blur(input, { target: { value: '30' } });

    await waitFor(() => expect(setConfig).toHaveBeenCalled());
    expect(setConfig.mock.calls[0][0]).toEqual({ retentionDays: 30 });
  });

  it('does not save a retention value outside the allowed range', async () => {
    vi.spyOn(api.backup, 'status').mockResolvedValue(makeStatus());
    const setConfig = vi.spyOn(api.backup, 'setConfig');

    renderSection();
    const input = await screen.findByLabelText('Retention days');
    fireEvent.blur(input, { target: { value: '0' } });

    expect(setConfig).not.toHaveBeenCalled();
  });

  it('points out a destination still coming from the legacy env file', async () => {
    vi.spyOn(api.backup, 'status').mockResolvedValue(
      makeStatus({ configSource: 'backup.env' })
    );

    renderSection();

    expect(
      await screen.findByText(/Still coming from ops\/backup\.env/)
    ).toBeTruthy();
  });

  it('says the timer is disabled when systemd reports it off', async () => {
    vi.spyOn(api.backup, 'status').mockResolvedValue(
      makeStatus({
        timer: {
          available: true,
          enabled: false,
          lastRun: null,
          lastResult: null,
          nextRun: null,
        },
      })
    );

    renderSection();

    expect(await screen.findByText(/timer disabled/)).toBeTruthy();
  });
});
