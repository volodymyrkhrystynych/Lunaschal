// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { FolderBar } from './Folders';

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
    },
  },
}));

const folder = (id: string, name: string, position: number) => ({
  id, name, position, ficCount: 0, createdAt: '', updatedAt: '',
});

beforeEach(() => {
  vi.mocked(api.fanfic.folders.reorder).mockClear();
  vi.mocked(api.fanfic.folders.list).mockResolvedValue([
    folder('f1', 'First', 0),
    folder('f2', 'Second', 1),
    folder('f3', 'Third', 2),
  ]);
});

function renderBar(folderId: string | null) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <FolderBar folderId={folderId} onSelect={() => {}} />
    </QueryClientProvider>,
  );
}

describe('FolderBar reordering', () => {
  it('moves the active folder earlier with the full new order', async () => {
    renderBar('f2');
    fireEvent.click(await screen.findByTitle(/Move folder earlier/));
    await waitFor(() =>
      expect(api.fanfic.folders.reorder).toHaveBeenCalledWith(['f2', 'f1', 'f3']));
  });

  it('moves the active folder later with the full new order', async () => {
    renderBar('f2');
    fireEvent.click(await screen.findByTitle(/Move folder later/));
    await waitFor(() =>
      expect(api.fanfic.folders.reorder).toHaveBeenCalledWith(['f1', 'f3', 'f2']));
  });

  it('disables the edge buttons for the first and last folder', async () => {
    const { unmount } = renderBar('f1');
    expect((await screen.findByTitle(/Move folder earlier/)).hasAttribute('disabled')).toBe(true);
    expect((await screen.findByTitle(/Move folder later/)).hasAttribute('disabled')).toBe(false);
    unmount();

    renderBar('f3');
    expect((await screen.findByTitle(/Move folder later/)).hasAttribute('disabled')).toBe(true);
    expect((await screen.findByTitle(/Move folder earlier/)).hasAttribute('disabled')).toBe(false);
  });

  it('shows no move buttons when no folder is selected', async () => {
    renderBar(null);
    await screen.findByText('First');
    expect(screen.queryByTitle(/Move folder/)).toBeNull();
  });
});
