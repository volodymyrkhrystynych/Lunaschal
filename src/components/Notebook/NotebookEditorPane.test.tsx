// @vitest-environment jsdom
import type { ComponentProps } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { NotebookEditorPane } from './NotebookEditorPane';

const { mocks } = vi.hoisted(() => ({
  mocks: {
    read: vi.fn(),
    write: vi.fn(),
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

  it('does nothing on :q when already on the index page', async () => {
    mocks.read.mockResolvedValue({ content: 'home' });
    const onOpenPath = vi.fn();
    const { container } = renderPane({ filePath: 'index.md', onOpenPath });

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
});
