// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ShortcutProvider } from '../../shortcuts/ShortcutProvider';
import { Notebook } from './Notebook';
import type { ComponentProps } from 'react';
import type { NotebookEditorPane as NotebookEditorPaneType } from './NotebookEditorPane';

const { mocks } = vi.hoisted(() => ({
  mocks: {
    ensure: vi.fn(),
    tree: vi.fn(),
    read: vi.fn(),
    write: vi.fn(),
    due: vi.fn(),
  },
}));

vi.mock('../../hooks/api', () => ({
  api: {
    shortcuts: { get: vi.fn().mockResolvedValue({ bindings: {} }) },
    settings: { get: vi.fn().mockResolvedValue({}) },
    notebook: {
      files: {
        ensure: mocks.ensure,
        tree: mocks.tree,
        read: mocks.read,
        write: mocks.write,
      },
      review: { due: mocks.due },
    },
  },
}));

vi.mock('./NotebookEditorPane', () => ({
  NotebookEditorPane: (
    props: ComponentProps<typeof NotebookEditorPaneType>
  ) => (
    <div
      data-testid="editor-pane"
      data-file-path={props.filePath}
      data-auto-focus={String(props.autoFocus ?? false)}
      ref={() => {
        if (props.handle) props.handle.current = paneHandle;
      }}
    >
      <button onClick={() => props.onOpenPath('other.md')}>open-other</button>
      <button onClick={props.onGoBack}>go-back</button>
      <button onClick={props.onExit}>quit</button>
    </div>
  ),
}));

// Stands in for the real pane's imperative handle (focus/scroll live inside
// CodeMirror), so the shortcut wiring can be asserted without an editor.
const paneHandle = { focus: vi.fn(), scrollBy: vi.fn() };

function renderNotebook() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ShortcutProvider currentView="notebook" onViewChange={() => {}}>
        <Notebook />
      </ShortcutProvider>
    </QueryClientProvider>
  );
}

const key = (code: string) => fireEvent.keyDown(window, { code });

beforeEach(() => {
  vi.clearAllMocks();
  mocks.due.mockResolvedValue([]);
  mocks.ensure.mockResolvedValue(undefined);
  mocks.tree.mockResolvedValue({ entries: [], truncated: false });
  mocks.read.mockResolvedValue({ content: '' });
  mocks.write.mockResolvedValue({ success: true });
});

describe('Notebook', () => {
  it('ensures index.md exists on mount', async () => {
    renderNotebook();
    await waitFor(() => expect(mocks.ensure).toHaveBeenCalledWith('index.md'));
  });

  it('shows a loading state until the index is ensured, then opens it', async () => {
    let resolveEnsure!: () => void;
    mocks.ensure.mockReturnValue(
      new Promise<void>(resolve => {
        resolveEnsure = resolve;
      })
    );
    renderNotebook();

    expect(screen.getByText('Loading…')).toBeTruthy();
    expect(screen.queryByTestId('editor-pane')).toBeNull();

    resolveEnsure();
    const pane = await screen.findByTestId('editor-pane');
    expect(pane.dataset.filePath).toBe('index.md');
  });

  it('writes a generated file-tree block into index.md on mount', async () => {
    mocks.tree.mockResolvedValue({
      entries: [
        { path: 'index.md', isDir: false },
        { path: 'diary', isDir: true },
        { path: 'diary/2026-08-20.md', isDir: false },
      ],
      truncated: false,
    });
    renderNotebook();

    await waitFor(() => expect(mocks.write).toHaveBeenCalled());
    const [path, content] = mocks.write.mock.calls[0];
    expect(path).toBe('index.md');
    expect(content).toContain('- diary/');
    expect(content).toContain('[[/diary/2026-08-20.md|2026-08-20]]');
    // The index must not link to itself.
    expect(content).not.toContain('[[/index.md');
  });

  it("leaves the user's own index text alone", async () => {
    mocks.read.mockResolvedValue({
      content: '# Index\n\nPinned: [[/todo.md|todo]]\n',
    });
    mocks.tree.mockResolvedValue({
      entries: [{ path: 'a.md', isDir: false }],
      truncated: false,
    });
    renderNotebook();

    await waitFor(() => expect(mocks.write).toHaveBeenCalled());
    expect(mocks.write.mock.calls[0][1]).toContain('Pinned: [[/todo.md|todo]]');
  });

  it('skips the write when the tree is already up to date', async () => {
    mocks.tree.mockResolvedValue({
      entries: [{ path: 'a.md', isDir: false }],
      truncated: false,
    });
    // Whatever the first pass would have produced, fed back in as the file's
    // current content.
    const { unmount } = renderNotebook();
    await waitFor(() => expect(mocks.write).toHaveBeenCalled());
    const settled = mocks.write.mock.calls[0][1];
    unmount();

    mocks.write.mockClear();
    mocks.read.mockResolvedValue({ content: settled });
    renderNotebook();

    await waitFor(() => expect(mocks.tree).toHaveBeenCalledTimes(2));
    expect(mocks.write).not.toHaveBeenCalled();
  });

  it('refreshes the tree when navigating back to the index', async () => {
    renderNotebook();
    await screen.findByTestId('editor-pane');
    const treeCallsAtIndex = mocks.tree.mock.calls.length;

    screen.getByText('open-other').click();
    await waitFor(() =>
      expect(screen.getByTestId('editor-pane').dataset.filePath).toBe(
        'other.md'
      )
    );
    // Leaving the index needs no refresh — only returning to it does.
    expect(mocks.tree).toHaveBeenCalledTimes(treeCallsAtIndex);

    screen.getByText('go-back').click();
    await waitFor(() =>
      expect(mocks.tree.mock.calls.length).toBeGreaterThan(treeCallsAtIndex)
    );
  });

  it('still opens the index when the tree refresh fails', async () => {
    renderNotebook();
    await screen.findByTestId('editor-pane');

    screen.getByText('open-other').click();
    await waitFor(() =>
      expect(screen.getByTestId('editor-pane').dataset.filePath).toBe(
        'other.md'
      )
    );

    mocks.tree.mockRejectedValue(new Error('offline'));
    screen.getByText('go-back').click();
    await waitFor(() =>
      expect(screen.getByTestId('editor-pane').dataset.filePath).toBe(
        'index.md'
      )
    );
  });

  // The whole point of the Notebook tab used to be unreachable from the
  // keyboard: the pane grabbed focus on mount, vim swallowed every app
  // shortcut, and `:q` on the index re-opened the page it was already on.
  it('opens the tab without taking the keyboard, and hands it over on nav.in', async () => {
    renderNotebook();
    const pane = await screen.findByTestId('editor-pane');
    expect(pane.dataset.autoFocus).toBe('false');
    expect(paneHandle.focus).not.toHaveBeenCalled();

    key('KeyD'); // nav.in: sidebar → inside the tab
    key('KeyD'); // nav.in: into the buffer
    await waitFor(() => expect(paneHandle.focus).toHaveBeenCalled());
    expect(screen.getByTestId('editor-pane').dataset.autoFocus).toBe('true');
  });

  it('scrolls the note with nav.up/down while the editor is not focused', async () => {
    renderNotebook();
    await screen.findByTestId('editor-pane');

    key('KeyD');
    key('KeyS');
    key('KeyW');
    expect(paneHandle.scrollBy.mock.calls.map(c => c[0])).toEqual([120, -120]);
  });

  it('gives the keyboard back on :q from the index', async () => {
    renderNotebook();
    await screen.findByTestId('editor-pane');

    key('KeyD');
    key('KeyD');
    await waitFor(() =>
      expect(screen.getByTestId('editor-pane').dataset.autoFocus).toBe('true')
    );

    screen.getByText('quit').click();
    await waitFor(() =>
      expect(screen.getByTestId('editor-pane').dataset.autoFocus).toBe('false')
    );
    // …and the level lands inside the tab, so W/S scroll rather than cycling
    // away to another view.
    paneHandle.scrollBy.mockClear();
    key('KeyS');
    expect(paneHandle.scrollBy).toHaveBeenCalledWith(120);
  });

  it('keeps the keyboard when a link jump switches files', async () => {
    renderNotebook();
    await screen.findByTestId('editor-pane');

    screen.getByText('open-other').click();
    await waitFor(() =>
      expect(screen.getByTestId('editor-pane').dataset.filePath).toBe(
        'other.md'
      )
    );
    expect(screen.getByTestId('editor-pane').dataset.autoFocus).toBe('true');
  });

  it('switches the open file via onOpenPath and restores it via onGoBack', async () => {
    renderNotebook();
    const pane = await screen.findByTestId('editor-pane');
    expect(pane.dataset.filePath).toBe('index.md');

    screen.getByText('open-other').click();
    await waitFor(() =>
      expect(screen.getByTestId('editor-pane').dataset.filePath).toBe(
        'other.md'
      )
    );

    screen.getByText('go-back').click();
    await waitFor(() =>
      expect(screen.getByTestId('editor-pane').dataset.filePath).toBe(
        'index.md'
      )
    );
  });
});
