// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, type Goal } from '@/hooks/api';
import { GoalsCard } from './GoalsCard';

vi.mock('@/hooks/api', () => ({
  api: { goals: { list: vi.fn(), check: vi.fn(), update: vi.fn() } },
}));
const goal: Goal = {
  id: 'goal-one',
  title: 'Daily pushups',
  criteria: 'Do ten pushups today.',
  reward: 'Theater trip',
  startDate: '2026-10-16',
  targetDays: 7,
  status: 'active',
  earnedAt: null,
  redeemedAt: null,
  lastCheckedAt: null,
  currentStreak: 6,
  bestStreak: 6,
  needsReview: false,
  days: [],
};
function mount(onOpenEntry = vi.fn()) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <GoalsCard onOpenEntry={onOpenEntry} />
    </QueryClientProvider>
  );
  return onOpenEntry;
}
beforeEach(() => vi.resetAllMocks());

it('checks today on demand and shows the newly earned prize', async () => {
  vi.mocked(api.goals.list).mockResolvedValue([goal]);
  const earned: Goal = {
    ...goal,
    status: 'earned',
    currentStreak: 7,
    bestStreak: 7,
    earnedAt: '2026-10-22T12:00:00Z',
  };
  vi.mocked(api.goals.check).mockImplementation(async () => {
    vi.mocked(api.goals.list).mockResolvedValue([earned]);
    return earned;
  });
  mount();
  fireEvent.click(await screen.findByRole('button', { name: 'Check now' }));
  await screen.findByText('7/7 consecutive days · Reward earned');
  expect(api.goals.check).toHaveBeenCalledWith(goal.id);
  fireEvent.click(screen.getByRole('button', { name: 'Mark prize redeemed' }));
  await waitFor(() =>
    expect(api.goals.update).toHaveBeenCalledWith(goal.id, 'redeem')
  );
});

it('opens cited journal entries and distinguishes unresolved evidence', async () => {
  vi.mocked(api.goals.list).mockResolvedValue([
    {
      ...goal,
      days: [
        {
          date: '2026-10-22',
          verdict: 'uncertain',
          evidence: [],
          error: 'AI unavailable',
        },
        {
          date: '2026-10-21',
          verdict: 'met',
          error: null,
          evidence: [
            { kind: 'journal', id: 'entry-one', text: 'I did ten pushups.' },
          ],
        },
      ],
    },
  ]);
  const open = mount();
  await screen.findByText('1 day(s) need clearer evidence or another check.');
  fireEvent.click(screen.getByText('Daily evidence'));
  fireEvent.click(screen.getByRole('button', { name: 'Open journal entry' }));
  expect(open).toHaveBeenCalledWith('entry-one');
  expect(screen.getByText('I did ten pushups.')).toBeTruthy();
  expect(
    screen.queryByRole('button', { name: 'Mark prize redeemed' })
  ).toBeNull();
});

it('shows check failures without claiming the reward was earned', async () => {
  vi.mocked(api.goals.list).mockResolvedValue([goal]);
  vi.mocked(api.goals.check).mockRejectedValue(new Error('Server unavailable'));
  mount();
  fireEvent.click(await screen.findByRole('button', { name: 'Check now' }));
  expect((await screen.findByRole('alert')).textContent).toBe(
    'Server unavailable'
  );
  expect(
    screen.queryByRole('button', { name: 'Mark prize redeemed' })
  ).toBeNull();
});

it('provides a chat creation hint when no goals exist', async () => {
  vi.mocked(api.goals.list).mockResolvedValue([]);
  mount();
  expect(
    await screen.findByText(/Ask chat to track a daily goal/)
  ).toBeTruthy();
});
