// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { BackupBrowse } from '../../hooks/api';
import { api } from '../../hooks/api';
import { FolderPicker } from './FolderPicker';

function browse(overrides: Partial<BackupBrowse> = {}): BackupBrowse {
  return {
    path: '/media/expansion',
    parent: '/media',
    entries: [
      { name: 'lunaschal', path: '/media/expansion/lunaschal', writable: true },
      { name: 'Seagate', path: '/media/expansion/Seagate', writable: false },
    ],
    truncated: false,
    writable: true,
    isMount: true,
    suggestions: [
      { name: '/media', path: '/media' },
      { name: '/mnt', path: '/mnt' },
    ],
    ...overrides,
  };
}

function renderPicker(props: Partial<Parameters<typeof FolderPicker>[0]> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onSelect = vi.fn();
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={qc}>
      <FolderPicker
        initialPath="/media/expansion"
        onSelect={onSelect}
        onClose={onClose}
        {...props}
      />
    </QueryClientProvider>
  );
  return { onSelect, onClose };
}

beforeEach(() => vi.restoreAllMocks());

describe('FolderPicker', () => {
  it('lists the folders at the current path', async () => {
    vi.spyOn(api.backup, 'browse').mockResolvedValue(browse());

    renderPicker();

    expect(await screen.findByText('lunaschal/')).toBeTruthy();
    expect(screen.getByText('Seagate/')).toBeTruthy();
  });

  it('flags folders that cannot be written to', async () => {
    // Choosing an unwritable folder is the failure this whole panel exists to
    // catch, so it should be visible before the choice, not after.
    vi.spyOn(api.backup, 'browse').mockResolvedValue(browse());

    renderPicker();

    expect(await screen.findByText('not writable')).toBeTruthy();
  });

  it('descends into a folder when clicked', async () => {
    const spy = vi.spyOn(api.backup, 'browse').mockResolvedValue(browse());

    renderPicker();
    fireEvent.click(await screen.findByText('lunaschal/'));

    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith('/media/expansion/lunaschal')
    );
  });

  it('walks back up via the parent entry', async () => {
    const spy = vi.spyOn(api.backup, 'browse').mockResolvedValue(browse());

    renderPicker();
    fireEvent.click(await screen.findByText('../'));

    await waitFor(() => expect(spy).toHaveBeenCalledWith('/media'));
  });

  it('hides the parent entry at the filesystem root', async () => {
    vi.spyOn(api.backup, 'browse').mockResolvedValue(
      browse({ path: '/', parent: null })
    );

    renderPicker();

    await screen.findByText('lunaschal/');
    expect(screen.queryByText('../')).toBeNull();
  });

  it('jumps to a suggested mount root', async () => {
    // Walking from / to a USB drive every time would be miserable.
    const spy = vi.spyOn(api.backup, 'browse').mockResolvedValue(browse());

    renderPicker();
    fireEvent.click(await screen.findByRole('button', { name: '/mnt' }));

    await waitFor(() => expect(spy).toHaveBeenCalledWith('/mnt'));
  });

  it('returns the folder currently open, not one that was clicked into', async () => {
    vi.spyOn(api.backup, 'browse').mockResolvedValue(browse());
    const { onSelect } = renderPicker();

    // Wait for the listing: the confirm button stays disabled until the folder
    // has actually loaded, so there is nothing to return before then.
    await screen.findByText('lunaschal/');
    fireEvent.click(screen.getByRole('button', { name: 'Use this folder' }));

    expect(onSelect).toHaveBeenCalledWith('/media/expansion');
  });

  it('cannot confirm before the folder has loaded', () => {
    vi.spyOn(api.backup, 'browse').mockReturnValue(new Promise(() => {}));
    const { onSelect } = renderPicker();

    fireEvent.click(screen.getByRole('button', { name: 'Use this folder' }));

    expect(onSelect).not.toHaveBeenCalled();
  });

  it('warns when the open folder is not writable', async () => {
    vi.spyOn(api.backup, 'browse').mockResolvedValue(
      browse({ writable: false })
    );

    renderPicker();

    expect(await screen.findByText(/isn’t writable/)).toBeTruthy();
  });

  it('surfaces a permission error instead of rendering an empty list', async () => {
    vi.spyOn(api.backup, 'browse').mockRejectedValue(
      new Error('Permission denied reading /root')
    );

    renderPicker();

    expect(await screen.findByText(/Permission denied/)).toBeTruthy();
  });

  it('closes on Escape', async () => {
    vi.spyOn(api.backup, 'browse').mockResolvedValue(browse());
    const { onClose } = renderPicker();

    await screen.findByText('lunaschal/');
    fireEvent.keyDown(window, { key: 'Escape' });

    expect(onClose).toHaveBeenCalled();
  });
});
