// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ShortcutProvider } from '../../shortcuts/ShortcutProvider';
import { NotebookTree } from './NotebookTree';
import type { FileEntry } from '../../hooks/api';

const { mocks } = vi.hoisted(() => ({
  mocks: {
    list: vi.fn(),
    read: vi.fn(),
    write: vi.fn(),
    rename: vi.fn(),
    delete: vi.fn(),
    mkdir: vi.fn(),
  },
}));

vi.mock('../../hooks/api', () => ({
  api: {
    notebook: {
      files: mocks,
      review: {
        getState: vi.fn().mockResolvedValue({ enabled: false, due: null }),
        due: vi.fn().mockResolvedValue([]),
      },
    },
    shortcuts: { get: vi.fn().mockResolvedValue({ bindings: {} }) },
    settings: { get: vi.fn().mockResolvedValue({}) },
  },
}));

const ROOT: FileEntry[] = [
  { name: 'ideas', path: 'ideas', isDir: true, size: null, modified: 0 },
  { name: 'todo.md', path: 'todo.md', isDir: false, size: 0, modified: 0 },
];
const IDEAS_CHILDREN: FileEntry[] = [
  {
    name: 'first.md',
    path: 'ideas/first.md',
    isDir: false,
    size: 0,
    modified: 0,
  },
];

function renderTree(onSelectFile = vi.fn()) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return {
    onSelectFile,
    ...render(
      <QueryClientProvider client={queryClient}>
        <ShortcutProvider currentView="notebook" onViewChange={() => {}}>
          <NotebookTree selectedPath={null} onSelectFile={onSelectFile} />
        </ShortcutProvider>
      </QueryClientProvider>
    ),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  Element.prototype.scrollIntoView = vi.fn();
  mocks.list.mockImplementation((path?: string) => {
    if (!path) return Promise.resolve(ROOT);
    if (path === 'ideas') return Promise.resolve(IDEAS_CHILDREN);
    return Promise.resolve([]);
  });
});

describe('NotebookTree', () => {
  it('lists root entries', async () => {
    renderTree();
    expect(await screen.findByText('ideas')).toBeTruthy();
    expect(screen.getByText('todo.md')).toBeTruthy();
  });

  it('D drills into a file and selects it, matching the app-wide nav.in binding', async () => {
    const { onSelectFile } = renderTree();
    await screen.findByText('ideas');

    // 'ideas' is focused first (index 0); move down to 'todo.md' then drill in.
    fireEvent.keyDown(window, { code: 'KeyD' }); // level 0 -> 1
    fireEvent.keyDown(window, { code: 'KeyS' }); // focus todo.md
    fireEvent.keyDown(window, { code: 'KeyD' }); // drill into the file

    await waitFor(() => expect(onSelectFile).toHaveBeenCalledWith('todo.md'));
  });

  it('Space expands a focused folder to reveal its children', async () => {
    renderTree();
    await screen.findByText('ideas');

    fireEvent.keyDown(window, { code: 'KeyD' }); // level 0 -> 1, focus on 'ideas'
    fireEvent.keyDown(window, { code: 'Space' });

    expect(await screen.findByText('first.md')).toBeTruthy();
  });

  it('/ opens a filter box that narrows the visible entries by name', async () => {
    renderTree();
    await screen.findByText('ideas');

    fireEvent.keyDown(window, { code: 'KeyD' }); // level 0 -> 1
    fireEvent.keyDown(window, { key: '/' });

    const input = await screen.findByPlaceholderText('/ search…');
    fireEvent.change(input, { target: { value: 'todo' } });

    expect(screen.getByText('todo.md')).toBeTruthy();
    expect(screen.queryByText('ideas')).toBeNull();
  });
});
