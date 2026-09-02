// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import type { PianoArchiveItem, PianoArchiveStatus } from '../../lib/piano';
import { PianoArchive } from './PianoArchive';

function status(
  overrides: Partial<PianoArchiveStatus> = {}
): PianoArchiveStatus {
  return {
    configured: true,
    available: true,
    writable: true,
    root: '/media/expansion/lunaschal/archive/piano',
    destination: '/media/expansion/lunaschal',
    reason: null,
    itemCount: 1,
    favoriteCount: 0,
    sizeBytes: 4096,
    freeBytes: 7 * 1024 ** 4,
    totalBytes: 8 * 1024 ** 4,
    ...overrides,
  };
}

function item(overrides: Partial<PianoArchiveItem> = {}): PianoArchiveItem {
  return {
    id: 'archive-1',
    collection: 'piano',
    title: 'Invention No. 1',
    creator: 'J. S. Bach',
    mediaType: 'score',
    sourceFilename: 'invention.musicxml',
    relativePath: 'downloads/invention.musicxml',
    sourceUrl: null,
    contentType: 'application/xml',
    sizeBytes: 4096,
    sha256: null,
    practiceCompatible: 1,
    favorite: 0,
    pianoPieceId: null,
    available: true,
    fileUrl: '/api/piano/archive/items/archive-1/file',
    createdAt: '2026-09-01T00:00:00+00:00',
    updatedAt: '2026-09-01T00:00:00+00:00',
    ...overrides,
  };
}

function renderArchive(
  onLibraryChanged = vi.fn().mockResolvedValue(undefined)
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <PianoArchive onLibraryChanged={onLibraryChanged} />
    </QueryClientProvider>
  );
  return { onLibraryChanged };
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api.piano, 'archiveStatus').mockResolvedValue(status());
  vi.spyOn(api.piano, 'archiveItems').mockResolvedValue({
    items: [item()],
    total: 1,
    limit: 100,
    offset: 0,
  });
});

describe('PianoArchive', () => {
  it('shows the external archive location and catalog entries', async () => {
    renderArchive();

    expect(await screen.findByText('Invention No. 1')).toBeTruthy();
    expect(
      screen.getByText('/media/expansion/lunaschal/archive/piano')
    ).toBeTruthy();
    expect(screen.getByText('Drive connected')).toBeTruthy();
    expect(screen.getAllByText('4.0 KB')).toHaveLength(2);
  });

  it('favorites compatible MusicXML into the practice library', async () => {
    const promoted = item({ favorite: 1, pianoPieceId: 'piece-1' });
    const favorite = vi
      .spyOn(api.piano, 'archiveFavorite')
      .mockResolvedValue(promoted);
    const { onLibraryChanged } = renderArchive();

    fireEvent.click(
      await screen.findByRole('button', { name: 'Favorite Invention No. 1' })
    );

    await waitFor(() =>
      expect(favorite).toHaveBeenCalledWith('archive-1', true)
    );
    await waitFor(() => expect(onLibraryChanged).toHaveBeenCalled());
    expect(
      await screen.findByText(/Added “Invention No. 1” to the practice library/)
    ).toBeTruthy();
  });

  it('rescans files copied directly onto the drive', async () => {
    const scan = vi.spyOn(api.piano, 'archiveScan').mockResolvedValue({
      indexed: 42,
      updated: 3,
      skipped: 0,
    });
    renderArchive();

    await screen.findByText('Drive connected');
    fireEvent.click(
      screen.getByRole('button', { name: 'Scan archive folder' })
    );

    await waitFor(() => expect(scan).toHaveBeenCalled());
    expect(
      await screen.findByText('Scan complete: 42 new, 3 updated.')
    ).toBeTruthy();
  });

  it('disables writes and explains when the drive is disconnected', async () => {
    vi.spyOn(api.piano, 'archiveStatus').mockResolvedValue(
      status({
        available: false,
        writable: false,
        reason: 'The backup drive is not connected.',
      })
    );
    renderArchive();

    expect(await screen.findByText('Drive unavailable')).toBeTruthy();
    expect(screen.getByText('The backup drive is not connected.')).toBeTruthy();
    expect(
      (
        screen.getByRole('button', {
          name: 'Scan archive folder',
        }) as HTMLButtonElement
      ).disabled
    ).toBe(true);
  });
});
