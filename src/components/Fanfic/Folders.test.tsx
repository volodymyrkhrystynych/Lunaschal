// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import type { Fic } from '../../hooks/api';
import { FolderBar, FolderPicker } from './Folders';

vi.mock('../../hooks/api', () => ({
  api: {
    fanfic: {
      folders: {
        list: vi.fn(),
        create: vi.fn(),
        rename: vi.fn(),
        reorder: vi.fn().mockResolvedValue({ success: true }),
        delete: vi.fn(),
      },
      addToFolder: vi.fn().mockResolvedValue({ success: true }),
      removeFromFolder: vi.fn().mockResolvedValue({ success: true }),
    },
  },
}));

const folder = (id: string, name: string, position: number) => ({
  id,
  name,
  position,
  ficCount: 0,
  createdAt: '',
  updatedAt: '',
});

beforeEach(() => {
  vi.mocked(api.fanfic.folders.reorder).mockClear();
  vi.mocked(api.fanfic.folders.list).mockResolvedValue([
    folder('f1', 'First', 0),
    folder('f2', 'Second', 1),
    folder('f3', 'Third', 2),
  ]);
});

function renderBar(
  folderId: string | null,
  onSelect: (id: string | null) => void = () => {}
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <FolderBar folderId={folderId} onSelect={onSelect} />
    </QueryClientProvider>
  );
}

describe('FolderBar reordering', () => {
  it('moves the active folder earlier with the full new order', async () => {
    renderBar('f2');
    fireEvent.click(await screen.findByTitle(/Move folder earlier/));
    await waitFor(() =>
      expect(api.fanfic.folders.reorder).toHaveBeenCalledWith([
        'f2',
        'f1',
        'f3',
      ])
    );
  });

  it('moves the active folder later with the full new order', async () => {
    renderBar('f2');
    fireEvent.click(await screen.findByTitle(/Move folder later/));
    await waitFor(() =>
      expect(api.fanfic.folders.reorder).toHaveBeenCalledWith([
        'f1',
        'f3',
        'f2',
      ])
    );
  });

  it('disables the edge buttons for the first and last folder', async () => {
    const { unmount } = renderBar('f1');
    expect(
      (await screen.findByTitle(/Move folder earlier/)).hasAttribute('disabled')
    ).toBe(true);
    expect(
      (await screen.findByTitle(/Move folder later/)).hasAttribute('disabled')
    ).toBe(false);
    unmount();

    renderBar('f3');
    expect(
      (await screen.findByTitle(/Move folder later/)).hasAttribute('disabled')
    ).toBe(true);
    expect(
      (await screen.findByTitle(/Move folder earlier/)).hasAttribute('disabled')
    ).toBe(false);
  });

  it('shows no move buttons when no folder is selected', async () => {
    renderBar(null);
    await screen.findByText('First');
    expect(screen.queryByTitle(/Move folder/)).toBeNull();
  });
});

describe('Recent pill', () => {
  it('selects the recent sentinel and hides move buttons', async () => {
    const onSelect = vi.fn();
    renderBar(null, onSelect);
    fireEvent.click(await screen.findByText('Recent'));
    expect(onSelect).toHaveBeenCalledWith('recent');
  });

  it('deselects back to All when clicked again while active', async () => {
    const onSelect = vi.fn();
    renderBar('recent', onSelect);
    fireEvent.click(await screen.findByText('Recent'));
    expect(onSelect).toHaveBeenCalledWith(null);
    expect(screen.queryByTitle(/Move folder/)).toBeNull();
  });
});

describe('FolderPicker', () => {
  const fic = { id: 'fic1', folderIds: [] } as unknown as Fic;

  function renderPicker() {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
    render(
      <QueryClientProvider client={queryClient}>
        <FolderPicker fic={fic} />
      </QueryClientProvider>
    );
    return { invalidateSpy };
  }

  it('checking a folder does not refresh the fic list while the menu stays open', async () => {
    const { invalidateSpy } = renderPicker();
    fireEvent.click(await screen.findByTitle('Add to folders'));
    fireEvent.click(await screen.findByLabelText('First'));

    await waitFor(() =>
      expect(api.fanfic.addToFolder).toHaveBeenCalledWith('fic1', 'f1')
    );
    // Persisted immediately, but the fic list itself must not be told to
    // refetch yet — that would yank an Unsorted fic out of the list the user
    // is still picking folders in.
    expect(invalidateSpy).not.toHaveBeenCalledWith({ queryKey: ['fanfic'] });
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ['fanfic', 'folders'],
    });
  });

  it('the checkbox reflects the just-made choice without waiting on a refetch', async () => {
    renderPicker();
    fireEvent.click(await screen.findByTitle('Add to folders'));
    const checkbox = (await screen.findByLabelText(
      'First'
    )) as HTMLInputElement;

    expect(checkbox.checked).toBe(false);
    fireEvent.click(checkbox);
    expect(checkbox.checked).toBe(true);
  });

  it('refreshes the fic list once the menu closes', async () => {
    const { invalidateSpy } = renderPicker();
    const button = await screen.findByTitle('Add to folders');
    fireEvent.click(button);
    fireEvent.click(await screen.findByLabelText('First'));
    invalidateSpy.mockClear();

    fireEvent.click(button);

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['fanfic'] });
  });

  it('closing via the backdrop also refreshes the fic list', async () => {
    const { invalidateSpy } = renderPicker();
    fireEvent.click(await screen.findByTitle('Add to folders'));
    invalidateSpy.mockClear();

    // The backdrop is the only other element painted at inset-0; find it by
    // its fixed positioning class rather than by role since it's a bare div.
    const backdrop = document.querySelector('.fixed.inset-0');
    expect(backdrop).not.toBeNull();
    fireEvent.click(backdrop as Element);

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['fanfic'] });
  });
});
