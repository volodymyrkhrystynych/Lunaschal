// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ShortcutProvider } from '../../shortcuts/ShortcutProvider';
import { FileTree } from './FileTree';
import type { FileEntry } from '../../hooks/api';

const { mocks } = vi.hoisted(() => ({
  mocks: {
    list: vi.fn(),
    read: vi.fn(),
    write: vi.fn(),
    rename: vi.fn(),
    delete: vi.fn(),
    mkdir: vi.fn(),
    upload: vi.fn(),
    contentUrl: vi.fn(),
  },
}));

vi.mock('../../hooks/api', () => ({
  api: {
    files: mocks,
    shortcuts: { get: vi.fn().mockResolvedValue({ bindings: {} }) },
    settings: { get: vi.fn().mockResolvedValue({}) },
  },
}));

const ROOT: FileEntry[] = [
  { name: 'notes', path: 'notes', isDir: true, size: null, modified: 0 },
  { name: 'todo.md', path: 'todo.md', isDir: false, size: 0, modified: 0 },
];
const NOTES_CHILDREN: FileEntry[] = [
  {
    name: 'first.md',
    path: 'notes/first.md',
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
        <ShortcutProvider currentView="files" onViewChange={() => {}}>
          <FileTree selectedPath={null} onSelectFile={onSelectFile} />
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
    if (path === 'notes') return Promise.resolve(NOTES_CHILDREN);
    return Promise.resolve([]);
  });
  mocks.upload.mockResolvedValue({ uploaded: [], errors: [] });
});

function fileList(files: File[]): FileList {
  const list = files.slice() as unknown as FileList & {
    item: (i: number) => File | null;
  };
  Object.defineProperty(list, 'item', {
    value: (i: number) => files[i] ?? null,
  });
  return list;
}

describe('FileTree', () => {
  it('lists root entries', async () => {
    renderTree();
    expect(await screen.findByText('notes')).toBeTruthy();
    expect(screen.getByText('todo.md')).toBeTruthy();
  });

  it('D drills into a file and selects it, matching the app-wide nav.in binding', async () => {
    const { onSelectFile } = renderTree();
    await screen.findByText('notes');

    // 'notes' is focused first (index 0); move down to 'todo.md' then drill in.
    fireEvent.keyDown(window, { code: 'KeyD' }); // level 0 -> 1
    fireEvent.keyDown(window, { code: 'KeyS' }); // focus todo.md
    fireEvent.keyDown(window, { code: 'KeyD' }); // drill into the file

    await waitFor(() => expect(onSelectFile).toHaveBeenCalledWith('todo.md'));
  });

  it('D on a collapsed folder expands it instead of selecting a file', async () => {
    renderTree();
    await screen.findByText('notes');

    fireEvent.keyDown(window, { code: 'KeyD' }); // level 0 -> 1, focus 'notes'
    fireEvent.keyDown(window, { code: 'KeyD' }); // drill in: expand 'notes'

    expect(await screen.findByText('first.md')).toBeTruthy();
  });

  it('A drills back out, collapsing an expanded folder', async () => {
    renderTree();
    await screen.findByText('notes');

    fireEvent.keyDown(window, { code: 'KeyD' }); // level 0 -> 1
    fireEvent.keyDown(window, { code: 'KeyD' }); // expand 'notes'
    await screen.findByText('first.md');

    fireEvent.keyDown(window, { code: 'KeyA' }); // drill out: collapse 'notes'

    await waitFor(() => expect(screen.queryByText('first.md')).toBeNull());
  });

  it('shows the empty state when there are no files', async () => {
    mocks.list.mockResolvedValue([]);
    renderTree();
    expect(
      await screen.findByText(
        'No files yet. Click "+ New", "Upload", or drop files here.'
      )
    ).toBeTruthy();
  });

  it('uploads files picked via the Upload button', async () => {
    renderTree();
    await screen.findByText('todo.md');

    const file = new File(['hello'], 'hello.txt', { type: 'text/plain' });
    const input = document.querySelector(
      'input[type="file"]'
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { files: fileList([file]) } });

    await waitFor(() => expect(mocks.upload).toHaveBeenCalledWith('', [file]));
  });

  it('uploads files dropped onto the tree root', async () => {
    renderTree();
    const list = await screen.findByText('todo.md');
    const file = new File(['hello'], 'photo.jpg', { type: 'image/jpeg' });

    fireEvent.drop(list.closest('.overflow-y-auto')!, {
      dataTransfer: { files: fileList([file]) },
    });

    await waitFor(() => expect(mocks.upload).toHaveBeenCalledWith('', [file]));
  });

  it('uploads files dropped onto a folder row into that folder', async () => {
    renderTree();
    const notesRow = await screen.findByText('notes');
    const file = new File(['hello'], 'photo.jpg', { type: 'image/jpeg' });

    fireEvent.drop(notesRow.closest('div')!, {
      dataTransfer: { files: fileList([file]) },
    });

    await waitFor(() =>
      expect(mocks.upload).toHaveBeenCalledWith('notes', [file])
    );
  });
});
