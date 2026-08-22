// @vitest-environment jsdom
import type { ComponentProps } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { NotebookEditorPane } from './NotebookEditorPane';
import type { NotebookPaneHandle } from './NotebookEditorPane';

const { mocks } = vi.hoisted(() => ({
  mocks: {
    read: vi.fn(),
    write: vi.fn(),
    tree: vi.fn(),
    getState: vi.fn(),
    toggle: vi.fn(),
  },
}));

vi.mock('../../hooks/api', () => ({
  api: {
    notebook: {
      files: {
        read: mocks.read,
        write: mocks.write,
        tree: mocks.tree,
        ensure: async (path: string) => {
          try {
            await mocks.read(path);
          } catch {
            await mocks.write(path, '');
          }
        },
      },
      review: { getState: mocks.getState, toggle: mocks.toggle },
    },
  },
}));

function renderPane(
  props: Partial<ComponentProps<typeof NotebookEditorPane>> = {}
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <NotebookEditorPane
        filePath="note.md"
        onOpenPath={() => {}}
        onGoBack={() => {}}
        {...props}
      />
    </QueryClientProvider>
  );
  return { queryClient, ...utils };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getState.mockResolvedValue({ enabled: false });
  mocks.write.mockResolvedValue({ success: true });
  mocks.tree.mockResolvedValue({ entries: [], truncated: false });
});

describe('NotebookEditorPane', () => {
  // Regression: an auto-save invalidates ['notebook'], which refetches the
  // file with its just-saved content. The editor must NOT be torn down and
  // rebuilt on that refetch — doing so resets the cursor to the start.
  it('does not rebuild the editor when the file refetches after a save', async () => {
    // First load returns the original text; any later refetch returns the
    // "saved" text (a different value), which the old code rebuilt on.
    mocks.read
      .mockResolvedValueOnce({ content: 'original' })
      .mockResolvedValue({ content: 'original edited' });

    const { container, queryClient } = renderPane();

    const editorEl = await waitFor(() => {
      const el = container.querySelector('.cm-editor');
      expect(el).toBeTruthy();
      return el as Element;
    });

    // Simulate the post-save refetch.
    await queryClient.invalidateQueries({ queryKey: ['notebook'] });
    await waitFor(() => expect(mocks.read).toHaveBeenCalledTimes(2));

    // Same DOM node ⇒ CodeMirror was never recreated (cursor preserved), and
    // the in-editor content is untouched by the refetch.
    expect(container.querySelector('.cm-editor')).toBe(editorEl);
    expect(container.querySelector('.cm-content')?.textContent).toContain(
      'original'
    );
    expect(container.querySelector('.cm-content')?.textContent).not.toContain(
      'edited'
    );
  });

  it('rebuilds the editor with fresh content when the file changes', async () => {
    mocks.read.mockResolvedValue({ content: 'file A body' });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { container, rerender } = render(
      <QueryClientProvider client={queryClient}>
        <NotebookEditorPane
          filePath="a.md"
          onOpenPath={() => {}}
          onGoBack={() => {}}
        />
      </QueryClientProvider>
    );
    await waitFor(() =>
      expect(container.querySelector('.cm-content')?.textContent).toContain(
        'file A body'
      )
    );

    mocks.read.mockResolvedValue({ content: 'file B body' });
    rerender(
      <QueryClientProvider client={queryClient}>
        <NotebookEditorPane
          filePath="b.md"
          onOpenPath={() => {}}
          onGoBack={() => {}}
        />
      </QueryClientProvider>
    );

    await waitFor(() =>
      expect(container.querySelector('.cm-content')?.textContent).toContain(
        'file B body'
      )
    );
  });

  it('toggles a checkbox on Ctrl+Space, cycling plain item -> unchecked -> checked', async () => {
    mocks.read.mockResolvedValue({ content: '- todo' });
    const { container } = renderPane();

    const content = await waitFor(() => {
      const el = container.querySelector('.cm-content');
      expect(el?.textContent).toContain('- todo');
      return el as Element;
    });

    fireEvent.keyDown(content, { key: ' ', code: 'Space', ctrlKey: true });
    await waitFor(() => expect(content.textContent).toContain('- [ ] todo'));

    fireEvent.keyDown(content, { key: ' ', code: 'Space', ctrlKey: true });
    await waitFor(() => expect(content.textContent).toContain('- [x] todo'));
  });

  it('follows a [[Link]] under the cursor on Enter, creating the target note', async () => {
    mocks.read.mockImplementation((path: string) =>
      path === 'note.md'
        ? Promise.resolve({ content: '[[Target Page]]' })
        : Promise.reject(new Error('Not found'))
    );
    const onOpenPath = vi.fn();
    const { container } = renderPane({ onOpenPath });

    const content = await waitFor(() => {
      const el = container.querySelector('.cm-content');
      expect(el?.textContent).toContain('Target Page');
      return el as Element;
    });

    fireEvent.keyDown(content, { key: 'Enter', code: 'Enter' });

    await waitFor(() =>
      expect(mocks.write).toHaveBeenCalledWith('Target Page.md', '')
    );
    await waitFor(() =>
      expect(onOpenPath).toHaveBeenCalledWith('Target Page.md')
    );
  });

  it("jumps to (and creates) today's diary note on <Space>w<Space>w", async () => {
    mocks.read.mockImplementation((path: string) =>
      path === 'note.md'
        ? Promise.resolve({ content: '' })
        : Promise.reject(new Error('Not found'))
    );
    const onOpenPath = vi.fn();
    const { container } = renderPane({ onOpenPath });

    const content = await waitFor(() => {
      const el = container.querySelector('.cm-content');
      expect(el).toBeTruthy();
      return el as Element;
    });

    fireEvent.keyDown(content, { key: ' ', code: 'Space' });
    fireEvent.keyDown(content, { key: 'w', code: 'KeyW' });
    fireEvent.keyDown(content, { key: ' ', code: 'Space' });
    fireEvent.keyDown(content, { key: 'w', code: 'KeyW' });

    await waitFor(() => expect(onOpenPath).toHaveBeenCalled());
    const [openedPath] = onOpenPath.mock.calls[0];
    expect(openedPath).toMatch(/^diary\/\d{4}-\d{2}-\d{2}\.md$/);
    expect(mocks.write).toHaveBeenCalledWith(openedPath, '');
  });

  it('navigates to the index page on :q, creating it if missing', async () => {
    mocks.read.mockImplementation((path: string) =>
      path === 'note.md'
        ? Promise.resolve({ content: 'some text' })
        : Promise.reject(new Error('Not found'))
    );
    const onOpenPath = vi.fn();
    const { container } = renderPane({ onOpenPath });

    const content = await waitFor(() => {
      const el = container.querySelector('.cm-content');
      expect(el?.textContent).toContain('some text');
      return el as Element;
    });

    fireEvent.keyDown(content, { key: ':', code: 'Semicolon' });
    const exInput = await waitFor(() => {
      const el = container.querySelector('.cm-vim-panel input');
      expect(el).toBeTruthy();
      return el as HTMLInputElement;
    });
    // jsdom keydown events don't drive native text-input editing, so type the
    // command by setting the input's value directly, then submit with Enter
    // — codemirror-vim's ex-mode dialog reads e.keyCode (legacy CM5 style),
    // which RTL's fireEvent doesn't infer from `key` alone.
    fireEvent.change(exInput, { target: { value: 'q' } });
    fireEvent.keyDown(exInput, { key: 'Enter', code: 'Enter', keyCode: 13 });

    await waitFor(() => expect(onOpenPath).toHaveBeenCalledWith('index.md'));
    expect(mocks.write).toHaveBeenCalledWith('index.md', '');
  });

  // The index is the end of the road: re-opening it was indistinguishable
  // from `:q` doing nothing, and left vim holding every key the app's own
  // shortcuts needed.
  it('releases the keyboard on :q from the index page', async () => {
    mocks.read.mockResolvedValue({ content: 'home' });
    const onOpenPath = vi.fn();
    const onExit = vi.fn();
    const { container } = renderPane({
      filePath: 'index.md',
      onOpenPath,
      onExit,
      autoFocus: true,
    });

    const content = await waitFor(() => {
      const el = container.querySelector('.cm-content');
      expect(el?.textContent).toContain('home');
      return el as Element;
    });

    fireEvent.keyDown(content, { key: ':', code: 'Semicolon' });
    const exInput = await waitFor(() => {
      const el = container.querySelector('.cm-vim-panel input');
      expect(el).toBeTruthy();
      return el as HTMLInputElement;
    });
    // jsdom keydown events don't drive native text-input editing, so type the
    // command by setting the input's value directly, then submit with Enter
    // — codemirror-vim's ex-mode dialog reads e.keyCode (legacy CM5 style),
    // which RTL's fireEvent doesn't infer from `key` alone.
    fireEvent.change(exInput, { target: { value: 'q' } });
    fireEvent.keyDown(exInput, { key: 'Enter', code: 'Enter', keyCode: 13 });

    await new Promise(resolve => setTimeout(resolve, 0));
    expect(onOpenPath).not.toHaveBeenCalled();
    expect(mocks.write).not.toHaveBeenCalled();
    expect(onExit).toHaveBeenCalled();
    // …and the editor really let go, rather than the ex dialog's own
    // re-focus putting it straight back.
    await waitFor(() =>
      expect(container.querySelector('.cm-content')).not.toBe(
        document.activeElement
      )
    );
  });

  // Arriving on the Notebook tab used to focus index.md's buffer, so every
  // app shortcut was swallowed before the user had done anything at all.
  it('does not take the keyboard unless asked to', async () => {
    mocks.read.mockResolvedValue({ content: 'home' });
    const { container } = renderPane({ filePath: 'index.md' });

    const content = await waitFor(() => {
      const el = container.querySelector('.cm-content');
      expect(el).toBeTruthy();
      return el as Element;
    });
    expect(document.activeElement).not.toBe(content);
  });

  it('focuses the editor through its handle', async () => {
    mocks.read.mockResolvedValue({ content: 'home' });
    const handle = { current: null } as {
      current: NotebookPaneHandle | null;
    };
    const { container } = renderPane({ filePath: 'index.md', handle });

    const content = await waitFor(() => {
      const el = container.querySelector('.cm-content');
      expect(el).toBeTruthy();
      return el as Element;
    });
    handle.current?.focus();
    expect(document.activeElement).toBe(content);
  });

  it('calls onGoBack on Backspace instead of moving the cursor', async () => {
    mocks.read.mockResolvedValue({ content: 'some text' });
    const onGoBack = vi.fn();
    const { container } = renderPane({ onGoBack });

    const content = await waitFor(() => {
      const el = container.querySelector('.cm-content');
      expect(el?.textContent).toContain('some text');
      return el as Element;
    });

    fireEvent.keyDown(content, { key: 'Backspace', code: 'Backspace' });
    await waitFor(() => expect(onGoBack).toHaveBeenCalledTimes(1));
  });

  it('highlights a [[Link]] span distinctly from surrounding text', async () => {
    mocks.read.mockResolvedValue({ content: 'see [[Some Page]] for more' });
    const { container } = renderPane();

    await waitFor(() => {
      const el = container.querySelector('.cm-content');
      expect(el?.textContent).toContain('Some Page');
    });

    const link = container.querySelector('.cm-wikilink');
    expect(link?.textContent).toBe('[[Some Page]]');
  });

  // `:find` — the filename search that vanished with the file-tree sidebar.
  describe(':find', () => {
    const NOTES = {
      entries: [
        { path: 'diary', isDir: true },
        { path: 'diary/2026-08-20.md', isDir: false },
        { path: 'diary/2026-08-19.md', isDir: false },
        { path: 'scratch.md', isDir: false },
      ],
      truncated: false,
    };

    /** Types `:<command><CR>` into codemirror-vim's ex dialog. */
    async function runEx(container: HTMLElement, command: string) {
      const content = await waitFor(() => {
        const el = container.querySelector('.cm-content');
        expect(el).toBeTruthy();
        return el as Element;
      });
      fireEvent.keyDown(content, { key: ':', code: 'Semicolon' });
      const exInput = await waitFor(() => {
        const el = container.querySelector('.cm-vim-panel input');
        expect(el).toBeTruthy();
        return el as HTMLInputElement;
      });
      fireEvent.change(exInput, { target: { value: command } });
      fireEvent.keyDown(exInput, { key: 'Enter', code: 'Enter', keyCode: 13 });
    }

    const findInput = (container: HTMLElement) =>
      container.querySelector(
        'input[placeholder="Find a note…"]'
      ) as HTMLInputElement | null;

    beforeEach(() => {
      mocks.read.mockResolvedValue({ content: 'body' });
      mocks.tree.mockResolvedValue(NOTES);
    });

    it('opens an unambiguous match directly, without a picker', async () => {
      const onOpenPath = vi.fn();
      const { container } = renderPane({ onOpenPath });

      await runEx(container, 'find scratch');

      await waitFor(() =>
        expect(onOpenPath).toHaveBeenCalledWith('scratch.md')
      );
      expect(findInput(container)).toBeNull();
    });

    it('shows a picker when several notes match, and opens the chosen one', async () => {
      const onOpenPath = vi.fn();
      const { container } = renderPane({ onOpenPath });

      await runEx(container, 'find diary');

      const input = await waitFor(() => {
        const el = findInput(container);
        expect(el).toBeTruthy();
        return el as HTMLInputElement;
      });
      expect(container.textContent).toContain('diary/2026-08-20.md');
      expect(container.textContent).toContain('diary/2026-08-19.md');
      // Directories are not openable notes, so they stay out of the results.
      expect(
        container.querySelectorAll('input[placeholder="Find a note…"] ~ ul li')
      ).toHaveLength(2);

      fireEvent.keyDown(input, { key: 'ArrowDown' });
      fireEvent.keyDown(input, { key: 'Enter' });

      await waitFor(() =>
        expect(onOpenPath).toHaveBeenCalledWith('diary/2026-08-19.md')
      );
      expect(findInput(container)).toBeNull();
    });

    it('moves the selection with Ctrl-n/Ctrl-p as well as the arrows', async () => {
      const onOpenPath = vi.fn();
      const { container } = renderPane({ onOpenPath });

      await runEx(container, 'find diary');
      const input = await waitFor(() => {
        const el = findInput(container);
        expect(el).toBeTruthy();
        return el as HTMLInputElement;
      });

      fireEvent.keyDown(input, { key: 'n', ctrlKey: true });
      fireEvent.keyDown(input, { key: 'p', ctrlKey: true });
      fireEvent.keyDown(input, { key: 'Enter' });

      await waitFor(() =>
        expect(onOpenPath).toHaveBeenCalledWith('diary/2026-08-20.md')
      );
    });

    it('re-filters live as the query is edited', async () => {
      const { container } = renderPane();

      await runEx(container, 'find diary');
      const input = await waitFor(() => {
        const el = findInput(container);
        expect(el).toBeTruthy();
        return el as HTMLInputElement;
      });

      fireEvent.change(input, { target: { value: '08-19' } });
      await waitFor(() => {
        expect(container.textContent).toContain('diary/2026-08-19.md');
        expect(container.textContent).not.toContain('diary/2026-08-20.md');
      });
    });

    it('lists every note when :find is given no query', async () => {
      const { container } = renderPane();

      await runEx(container, 'find');

      await waitFor(() => expect(findInput(container)).toBeTruthy());
      expect(container.textContent).toContain('scratch.md');
      expect(container.textContent).toContain('diary/2026-08-20.md');
    });

    it('says so rather than showing an empty list when nothing matches', async () => {
      const { container } = renderPane();

      await runEx(container, 'find nonexistent');

      await waitFor(() =>
        expect(container.textContent).toContain('No notes match.')
      );
    });

    it('accepts the :fin abbreviation, as real vim does', async () => {
      const onOpenPath = vi.fn();
      const { container } = renderPane({ onOpenPath });

      await runEx(container, 'fin scratch');

      await waitFor(() =>
        expect(onOpenPath).toHaveBeenCalledWith('scratch.md')
      );
    });

    it('closes on Escape without opening anything', async () => {
      const onOpenPath = vi.fn();
      const { container } = renderPane({ onOpenPath });

      await runEx(container, 'find diary');
      const input = await waitFor(() => {
        const el = findInput(container);
        expect(el).toBeTruthy();
        return el as HTMLInputElement;
      });

      fireEvent.keyDown(input, { key: 'Escape' });

      await waitFor(() => expect(findInput(container)).toBeNull());
      expect(onOpenPath).not.toHaveBeenCalled();
    });
  });
});
