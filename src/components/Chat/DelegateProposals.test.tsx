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
      proposal: { id: 'p1', kind: 'calendar', status: 'accepted', data: {} },
    } as never);
    const data = { title: 'Dentist appointment', date: '2026-08-05' };
    renderProposals([{ id: 'p1', kind: 'calendar', status: 'pending', data }]);

    fireEvent.click(screen.getByText('Save'));

    await waitFor(() =>
      expect(resolveProposal()).toHaveBeenCalledWith('m1', 'p1', 'accept', data)
    );
  });

  it('dismissing posts a dismiss action with no data', async () => {
    resolveProposal().mockResolvedValue({
      proposal: { id: 'p1', kind: 'calendar', status: 'dismissed', data: {} },
    } as never);
    renderProposals([
      {
        id: 'p1',
        kind: 'calendar',
        status: 'pending',
        data: { title: 'Dentist appointment', date: '2026-08-05' },
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
        kind: 'calendar',
        status: 'dismissed',
        data: { title: 'Dentist appointment' },
      },
    ]);

    expect(screen.getByText('Dismissed')).toBeTruthy();
    expect(screen.queryByText('Save as calendar event?')).toBeNull();
  });
});

describe('the food card', () => {
  const foodProposal = (
    data: Record<string, unknown> = {}
  ): DelegateProposalRecord => ({
    id: 'p1',
    kind: 'food',
    status: 'pending',
    data: {
      dish: 'Vareniki',
      place: 'Movati',
      notes: 'really good',
      calories: 600,
      rating: 4,
      tags: [],
      ...data,
    },
  });

  it('shows every field the model filled in', () => {
    renderProposals([foodProposal()]);
    expect((screen.getByLabelText('Dish') as HTMLInputElement).value).toBe(
      'Vareniki'
    );
    expect((screen.getByLabelText('Place') as HTMLInputElement).value).toBe(
      'Movati'
    );
    expect((screen.getByLabelText('Calories') as HTMLInputElement).value).toBe(
      '600'
    );
    expect((screen.getByLabelText('Rating') as HTMLInputElement).value).toBe(
      '4'
    );
  });

  it('says the photo and the exact words come along', () => {
    // Neither is an editable field: both are resolved from the message itself
    // at accept time, so an edit cannot rewrite what was actually said.
    renderProposals([foodProposal()]);
    expect(screen.getByText(/exactly what you said/i)).toBeTruthy();
  });

  it('posts the edited values, not what was staged', async () => {
    resolveProposal().mockResolvedValue({
      proposal: { id: 'p1', kind: 'food', status: 'accepted', data: {} },
    } as never);
    renderProposals([foodProposal()]);
    fireEvent.change(screen.getByLabelText('Dish'), {
      target: { value: 'Pierogi' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Log Meal' }));

    await waitFor(() =>
      expect(resolveProposal()).toHaveBeenCalledWith(
        'm1',
        'p1',
        'accept',
        expect.objectContaining({ dish: 'Pierogi' })
      )
    );
  });

  it('clears a calorie count rather than sending an empty string', async () => {
    // Most meals are logged without a number; the backend refuses anything that
    // is not an integer or null.
    resolveProposal().mockResolvedValue({
      proposal: { id: 'p1', kind: 'food', status: 'accepted', data: {} },
    } as never);
    renderProposals([foodProposal()]);
    fireEvent.change(screen.getByLabelText('Calories'), {
      target: { value: '' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Log Meal' }));

    await waitFor(() =>
      expect(resolveProposal()).toHaveBeenCalledWith(
        'm1',
        'p1',
        'accept',
        expect.objectContaining({ calories: null })
      )
    );
  });

  it('collapses to one line once accepted, saying whether calories went too', () => {
    renderProposals([
      {
        ...foodProposal(),
        status: 'accepted',
        result: { id: 'f1', photos: 1 },
      },
    ]);
    expect(screen.getByText('Saved to your food log')).toBeTruthy();

    renderProposals([
      {
        ...foodProposal(),
        status: 'accepted',
        result: { id: 'f1', photos: 1, calorieLogId: 'cl1' },
      },
    ]);
    expect(
      screen.getByText('Saved to your food log, with calories')
    ).toBeTruthy();
  });
});
