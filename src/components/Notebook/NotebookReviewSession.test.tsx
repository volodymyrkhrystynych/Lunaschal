// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ShortcutProvider } from '../../shortcuts/ShortcutProvider';
import { NotebookReviewSession } from './NotebookReviewSession';
import type { NotebookReviewState } from '../../hooks/api';

const { DUE, mocks } = vi.hoisted(() => {
  const DUE: NotebookReviewState[] = [
    {
      path: 'ideas/first.md',
      enabled: true,
      fsrsState: null,
      due: '2026-07-17T00:00:00Z',
    },
  ];
  const mocks = {
    filesRead: vi.fn(),
    due: vi.fn(),
    rate: vi.fn(),
  };
  return { DUE, mocks };
});

vi.mock('../../hooks/api', () => ({
  api: {
    notebook: {
      files: { read: mocks.filesRead },
      review: { due: mocks.due, rate: mocks.rate },
    },
    shortcuts: { get: vi.fn().mockResolvedValue({ bindings: {} }) },
    settings: { get: vi.fn().mockResolvedValue({}) },
  },
}));

function renderSession(onExit = vi.fn()) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ShortcutProvider currentView="notebook" onViewChange={() => {}}>
        <NotebookReviewSession onExit={onExit} />
      </ShortcutProvider>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.due.mockResolvedValue(DUE);
  mocks.filesRead.mockResolvedValue({ content: '# First idea\n\nSome notes.' });
  mocks.rate.mockResolvedValue({ due: 'later' });
});

describe('NotebookReviewSession', () => {
  it('renders the due file as markdown with the path shown', async () => {
    renderSession();
    const heading = await screen.findByText('First idea');
    expect(heading.tagName).toBe('H1');
    expect(screen.getByText('ideas/first.md')).toBeTruthy();
  });

  it('rates via button click', async () => {
    renderSession();
    await screen.findByText('First idea');
    fireEvent.click(screen.getByText('Easy'));
    await waitFor(() =>
      expect(mocks.rate).toHaveBeenCalledWith('ideas/first.md', 4)
    );
  });

  it('rates via digit key, matching the app-wide Learning rating convention', async () => {
    renderSession();
    await screen.findByText('First idea');
    fireEvent.keyDown(window, { code: 'Digit3' });
    await waitFor(() =>
      expect(mocks.rate).toHaveBeenCalledWith('ideas/first.md', 3)
    );
  });

  it('refetches and advances after a rating', async () => {
    mocks.due.mockResolvedValueOnce(DUE).mockResolvedValueOnce([]);
    const onExit = vi.fn();
    renderSession(onExit);
    await screen.findByText('First idea');

    fireEvent.click(screen.getByText('Good'));
    await waitFor(() => expect(mocks.rate).toHaveBeenCalled());
    expect(await screen.findByText('All caught up!')).toBeTruthy();
    // Reaching the empty state is a mode within this component, not a
    // keyboard drill-out — exiting is the explicit "Back to Notebook" button.
    expect(onExit).not.toHaveBeenCalled();
  });

  it('shows the all-caught-up state and exits via the button when nothing is due', async () => {
    mocks.due.mockResolvedValue([]);
    const onExit = vi.fn();
    renderSession(onExit);

    expect(await screen.findByText('All caught up!')).toBeTruthy();
    fireEvent.click(screen.getByText('Back to Notebook'));
    expect(onExit).toHaveBeenCalledTimes(1);
  });
});
