// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Notebook } from './Notebook';
import type { ComponentProps } from 'react';
import type { NotebookEditorPane as NotebookEditorPaneType } from './NotebookEditorPane';

const { mocks } = vi.hoisted(() => ({
  mocks: {
    ensure: vi.fn(),
    due: vi.fn(),
  },
}));

vi.mock('../../hooks/api', () => ({
  api: {
    notebook: {
      files: { ensure: mocks.ensure },
      review: { due: mocks.due },
    },
  },
}));

vi.mock('./NotebookEditorPane', () => ({
  NotebookEditorPane: (
    props: ComponentProps<typeof NotebookEditorPaneType>
  ) => (
    <div data-testid="editor-pane" data-file-path={props.filePath}>
      <button onClick={() => props.onOpenPath('other.md')}>open-other</button>
      <button onClick={props.onGoBack}>go-back</button>
    </div>
  ),
}));

function renderNotebook() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <Notebook />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.due.mockResolvedValue([]);
  mocks.ensure.mockResolvedValue(undefined);
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
