// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, type DelegateProposalRecord } from '../../hooks/api';
import { DelegateProposals } from './DelegateProposals';

vi.mock('../../hooks/api', () => ({
  api: {
    chat: { resolveProposal: vi.fn() },
  },
}));

beforeEach(() => {
  vi.clearAllMocks();
});

function renderProposals(proposals: DelegateProposalRecord[]) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <DelegateProposals messageId="m1" proposals={proposals} />
    </QueryClientProvider>
  );
}

const resolveProposal = () => vi.mocked(api.chat.resolveProposal);

describe('pending cards', () => {
  it('renders a calendar proposal with its title, date and tags', () => {
    renderProposals([
      {
        id: 'p1',
        kind: 'calendar',
        status: 'pending',
        data: {
          title: 'Dentist appointment',
          date: '2026-08-05',
          tags: ['health'],
        },
      },
    ]);

    expect(screen.getByText('Save as calendar event?')).toBeTruthy();
    expect(screen.getByDisplayValue('Dentist appointment')).toBeTruthy();
    expect(screen.getByDisplayValue('2026-08-05')).toBeTruthy();
    expect(screen.getByDisplayValue('health')).toBeTruthy();
  });

  it('renders a calorie proposal', () => {
    renderProposals([
      {
        id: 'p1',
        kind: 'calorie',
        status: 'pending',
        data: { description: 'burger', calories: 650 },
      },
    ]);

    expect(screen.getByText('Log calories?')).toBeTruthy();
    expect(screen.getByDisplayValue('burger')).toBeTruthy();
    expect(screen.getByDisplayValue('650')).toBeTruthy();
  });

  it('renders a task proposal with the due date and priority it was staged with', () => {
    // These two were not shown at all — and before that, not even carried —
    // so a to-do was confirmed without the user seeing what it was due.
    renderProposals([
      {
        id: 'p1',
        kind: 'task',
        status: 'pending',
        data: { title: 'call the dentist', due: '2026-08-14', priority: 5 },
      },
    ]);

    expect(screen.getByText('Add to your tasks?')).toBeTruthy();
    expect(screen.getByDisplayValue('call the dentist')).toBeTruthy();
    expect(screen.getByDisplayValue('2026-08-14')).toBeTruthy();
    expect(screen.getByDisplayValue('5 — Very important')).toBeTruthy();
  });

  it('renders a flashcards proposal', () => {
    renderProposals([
      {
        id: 'p1',
        kind: 'flashcards',
        status: 'pending',
        data: { topic: 'React hooks' },
      },
    ]);

    expect(screen.getByText('Generate flashcards?')).toBeTruthy();
    expect(screen.getByDisplayValue('React hooks')).toBeTruthy();
  });

  it('hides the clocks on an all-day event, which means the whole day', () => {
    renderProposals([
      {
        id: 'p1',
        kind: 'calendar',
        status: 'pending',
        data: { title: 'Holiday', date: '2026-08-05', allDay: true },
      },
    ]);

    expect(screen.queryByText('From')).toBeNull();
    fireEvent.click(screen.getByLabelText('All day'));
    expect(screen.getByText('From')).toBeTruthy();
  });

  it('accepting posts the card values for that proposal id', async () => {
    resolveProposal().mockResolvedValue({
      proposal: { id: 'p1', kind: 'task', status: 'accepted', data: {} },
    } as never);
    const data = { title: 'call the dentist', list: 'todo', priority: 3 };
    renderProposals([{ id: 'p1', kind: 'task', status: 'pending', data }]);

    fireEvent.click(screen.getByText('Add'));

    await waitFor(() =>
      expect(resolveProposal()).toHaveBeenCalledWith('m1', 'p1', 'accept', data)
    );
  });

  it('accepting an edited card posts the edit, not what was staged', async () => {
    resolveProposal().mockResolvedValue({
      proposal: { id: 'p1', kind: 'task', status: 'accepted', data: {} },
    } as never);
    renderProposals([
      {
        id: 'p1',
        kind: 'task',
        status: 'pending',
        data: {
          title: 'Book flights',
          list: 'todo',
          due: '2026-08-14',
          priority: 3,
        },
      },
    ]);

    fireEvent.change(screen.getByDisplayValue('2026-08-14'), {
      target: { value: '2026-08-20' },
    });
    fireEvent.change(screen.getByDisplayValue('3 — Normal'), {
      target: { value: '5' },
    });
    fireEvent.click(screen.getByText('Add'));

    await waitFor(() =>
      expect(resolveProposal()).toHaveBeenCalledWith(
        'm1',
        'p1',
        'accept',
        expect.objectContaining({ due: '2026-08-20', priority: 5 })
      )
    );
  });

  it('dismissing posts a dismiss action with no data', async () => {
    resolveProposal().mockResolvedValue({
      proposal: { id: 'p1', kind: 'task', status: 'dismissed', data: {} },
    } as never);
    renderProposals([
      {
        id: 'p1',
        kind: 'task',
        status: 'pending',
        data: { title: 'call the dentist' },
      },
    ]);

    fireEvent.click(screen.getByText('Dismiss'));

    await waitFor(() =>
      expect(resolveProposal()).toHaveBeenCalledWith(
        'm1',
        'p1',
        'dismiss',
        undefined
      )
    );
  });

  it('shows the server error inline and leaves the card actionable on failure', async () => {
    resolveProposal().mockRejectedValue(
      new Error('calories must be an integer from 0 to 20000')
    );
    renderProposals([
      {
        id: 'p1',
        kind: 'calorie',
        status: 'pending',
        data: { description: 'burger', calories: 650 },
      },
    ]);

    fireEvent.click(screen.getByText('Log'));

    expect(
      await screen.findByText('calories must be an integer from 0 to 20000')
    ).toBeTruthy();
    // Still pending — the user can retry rather than losing the card.
    expect(screen.getByText('Log')).toBeTruthy();
  });
});

describe('resolved cards', () => {
  it('collapses an accepted proposal to a quiet line per kind', () => {
    renderProposals([
      {
        id: 'p1',
        kind: 'calendar',
        status: 'accepted',
        data: { title: 'Dentist appointment' },
        result: { id: 'cal1' },
      },
      {
        id: 'p2',
        kind: 'flashcards',
        status: 'accepted',
        data: { topic: 'React hooks' },
        result: { count: 4 },
      },
    ]);

    expect(screen.getByText('Saved to calendar')).toBeTruthy();
    expect(
      screen.getByText('Queued 4 cards for review in Learning')
    ).toBeTruthy();
    expect(screen.queryByText('Save as calendar event?')).toBeNull();
    expect(screen.queryByText('Save')).toBeNull();
  });

  it('collapses a dismissed proposal to "Dismissed" regardless of kind', () => {
    renderProposals([
      {
        id: 'p1',
        kind: 'task',
        status: 'dismissed',
        data: { title: 'call the dentist' },
      },
    ]);

    expect(screen.getByText('Dismissed')).toBeTruthy();
    expect(screen.queryByText('Add to your tasks?')).toBeNull();
  });
});
