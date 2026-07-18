// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { ShortcutProvider, useShortcutScope } from '../../shortcuts/ShortcutProvider';
import { Queue } from './Queue';
import type { LearningCard } from '../../hooks/api';

const { QUEUE, mocks } = vi.hoisted(() => {
  const card = (id: string, extra: Partial<LearningCard> = {}): LearningCard => ({
    id,
    folderId: null,
    question: `Question ${id}?`,
    answer: `Answer ${id}.`,
    state: 'pending',
    tags: [],
    sourceType: 'braindump',
    sourceId: null,
    derivedFrom: null,
    revisedFrom: null,
    due: null,
    createdAt: '2026-07-17T10:00:00Z',
    updatedAt: '2026-07-17T10:00:00Z',
    ...extra,
  });
  const QUEUE = [card('c1'), card('c2', { derivedFrom: 'parent' })];
  const mocks = {
    listQueue: vi.fn(),
    approve: vi.fn(),
    regenerate: vi.fn(),
    deny: vi.fn(),
    deleteCard: vi.fn(),
  };
  return { QUEUE, mocks };
});

vi.mock('../../hooks/api', () => ({
  api: {
    learning: mocks,
    shortcuts: { get: vi.fn().mockResolvedValue({ bindings: {} }) },
  },
}));

// Stand-in for Learning.tsx's scope 1 so D can descend to the queue scope at depth 2.
function Scope1({ children }: { children: ReactNode }) {
  useShortcutScope(1, {});
  return <>{children}</>;
}

function renderQueue() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ShortcutProvider currentView="learning" onViewChange={() => {}}>
        <Scope1>
          <Queue />
        </Scope1>
      </ShortcutProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.listQueue.mockResolvedValue(QUEUE);
  Element.prototype.scrollIntoView = vi.fn();
});

describe('Queue', () => {
  it('lists pending cards with a follow-up badge on derived ones', async () => {
    renderQueue();
    expect(await screen.findByText('Question c1?')).toBeTruthy();
    expect(screen.getByText('Question c2?')).toBeTruthy();
    expect(screen.getAllByText('follow-up')).toHaveLength(1);
  });

  it('approves a card', async () => {
    mocks.approve.mockResolvedValue({ status: 'approved', due: 'now' });
    renderQueue();
    fireEvent.click((await screen.findAllByText('Approve'))[0]);
    await waitFor(() => expect(mocks.approve).toHaveBeenCalledWith('c1', undefined));
    expect(screen.queryByText('Possible duplicate')).toBeNull();
  });

  it('opens the duplicate-hint dialog and force-approves on "keep both"', async () => {
    mocks.approve
      .mockResolvedValueOnce({
        status: 'duplicateHint',
        similar: { id: 'old1', question: 'Existing?', answer: 'Existing answer.' },
        score: 0.91,
      })
      .mockResolvedValueOnce({ status: 'approved', due: 'now' });
    renderQueue();
    fireEvent.click((await screen.findAllByText('Approve'))[0]);

    expect(await screen.findByText('Possible duplicate')).toBeTruthy();
    expect(screen.getByText(/91% similar/)).toBeTruthy();
    expect(screen.getByText('Existing?')).toBeTruthy();

    fireEvent.click(screen.getByText("Keep both — they're distinct"));
    await waitFor(() => expect(mocks.approve).toHaveBeenLastCalledWith('c1', true));
  });

  it('deletes the old card then force-approves on "keep the new one"', async () => {
    mocks.approve
      .mockResolvedValueOnce({
        status: 'duplicateHint',
        similar: { id: 'old1', question: 'Existing?', answer: 'Existing answer.' },
        score: 0.85,
      })
      .mockResolvedValueOnce({ status: 'approved', due: 'now' });
    mocks.deleteCard.mockResolvedValue({ success: true });
    renderQueue();
    fireEvent.click((await screen.findAllByText('Approve'))[0]);
    fireEvent.click(await screen.findByText('Keep the new one, delete the old'));

    await waitFor(() => expect(mocks.deleteCard).toHaveBeenCalledWith('old1'));
    await waitFor(() => expect(mocks.approve).toHaveBeenLastCalledWith('c1', true));
  });

  it('submits steerable regeneration with the typed direction', async () => {
    mocks.regenerate.mockResolvedValue({ count: 2, ids: ['n1', 'n2'] });
    renderQueue();
    fireEvent.click((await screen.findAllByText('Regenerate…'))[0]);
    const input = screen.getByPlaceholderText(/split it/);
    fireEvent.change(input, { target: { value: 'too broad, split it' } });
    fireEvent.click(screen.getByText('Go'));
    await waitFor(() =>
      expect(mocks.regenerate).toHaveBeenCalledWith('c1', 'too broad, split it'));
  });

  it('denies a card', async () => {
    mocks.deny.mockResolvedValue({ success: true });
    renderQueue();
    fireEvent.click((await screen.findAllByText('Deny'))[0]);
    await waitFor(() => expect(mocks.deny).toHaveBeenCalledWith('c1'));
  });

  it('drives the queue with the keyboard: S selects, Y approves, X denies, I steers', async () => {
    mocks.approve.mockResolvedValue({ status: 'approved', due: 'now' });
    mocks.deny.mockResolvedValue({ success: true });
    renderQueue();
    await screen.findByText('Question c1?');

    fireEvent.keyDown(window, { code: 'KeyD' }); // level 0 -> 1
    fireEvent.keyDown(window, { code: 'KeyD' }); // level 1 -> 2
    fireEvent.keyDown(window, { code: 'KeyS' }); // select second card
    fireEvent.keyDown(window, { code: 'KeyY' });
    await waitFor(() => expect(mocks.approve).toHaveBeenCalledWith('c2', undefined));

    fireEvent.keyDown(window, { code: 'KeyX' });
    await waitFor(() => expect(mocks.deny).toHaveBeenCalledWith('c2'));

    fireEvent.keyDown(window, { code: 'KeyI' }); // steer-regenerate the selected card
    expect(await screen.findByPlaceholderText(/split it/)).toBeTruthy();
  });
});
