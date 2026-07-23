// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, waitFor } from '@testing-library/react';
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
      files: { read: mocks.read, write: mocks.write },
      review: { getState: mocks.getState, toggle: mocks.toggle },
    },
  },
}));

function renderPane() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <NotebookEditorPane filePath="note.md" onExit={() => {}} />
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
        <NotebookEditorPane filePath="a.md" onExit={() => {}} />
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
        <NotebookEditorPane filePath="b.md" onExit={() => {}} />
      </QueryClientProvider>
    );

    await waitFor(() =>
      expect(container.querySelector('.cm-content')?.textContent).toContain(
        'file B body'
      )
    );
  });
});
