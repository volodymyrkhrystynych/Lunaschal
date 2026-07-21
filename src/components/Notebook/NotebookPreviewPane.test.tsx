// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { NotebookPreviewPane } from './NotebookPreviewPane';
import type { FileEntry } from '../../hooks/api';

const { mocks } = vi.hoisted(() => ({
  mocks: { read: vi.fn(), list: vi.fn() },
}));

vi.mock('../../hooks/api', () => ({
  api: { notebook: { files: mocks } },
}));

function renderPane(entry: FileEntry | null) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <NotebookPreviewPane entry={entry} />
    </QueryClientProvider>
  );
}

const fileEntry: FileEntry = {
  name: 'note.md',
  path: 'note.md',
  isDir: false,
  size: 10,
  modified: 0,
};
const dirEntry: FileEntry = {
  name: 'ideas',
  path: 'ideas',
  isDir: true,
  size: null,
  modified: 0,
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('NotebookPreviewPane', () => {
  it('shows a placeholder when nothing is highlighted', () => {
    renderPane(null);
    expect(screen.getByText('Select a file to edit')).toBeTruthy();
    expect(mocks.read).not.toHaveBeenCalled();
    expect(mocks.list).not.toHaveBeenCalled();
  });

  it('renders a highlighted file as markdown', async () => {
    mocks.read.mockResolvedValue({ content: '# Hello\n\nsome **bold** text' });
    renderPane(fileEntry);

    expect(await screen.findByText('Hello')).toBeTruthy();
    expect(screen.getByText('bold')).toBeTruthy();
    expect(mocks.read).toHaveBeenCalledWith('note.md');
    expect(mocks.list).not.toHaveBeenCalled();
  });

  it('lists the children of a highlighted folder', async () => {
    mocks.list.mockResolvedValue([
      { name: 'sub', path: 'ideas/sub', isDir: true, size: null, modified: 0 },
      {
        name: 'first.md',
        path: 'ideas/first.md',
        isDir: false,
        size: 0,
        modified: 0,
      },
    ] satisfies FileEntry[]);
    renderPane(dirEntry);

    expect(await screen.findByText('sub/')).toBeTruthy();
    expect(screen.getByText('first.md')).toBeTruthy();
    expect(mocks.list).toHaveBeenCalledWith('ideas');
    expect(mocks.read).not.toHaveBeenCalled();
  });

  it('shows an empty-folder note when a folder has no children', async () => {
    mocks.list.mockResolvedValue([]);
    renderPane(dirEntry);
    expect(await screen.findByText('Empty folder.')).toBeTruthy();
  });
});
