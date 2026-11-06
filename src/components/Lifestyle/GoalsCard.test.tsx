// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, type CurrentGoals, type Goal } from '@/hooks/api';
import { GoalsCard } from './GoalsCard';

vi.mock('@/hooks/api', () => ({
  api: {
    goals: {
      current: vi.fn(),
      check: vi.fn(),
      update: vi.fn(),
      create: vi.fn(),
      setOverarching: vi.fn(),
      history: vi.fn(),
    },
  },
}));

const base: Goal = {
  id: 'goal-one',
  horizon: 'week',
  title: 'Daily pushups',
  details: null,
  criteria: 'Do ten pushups today.',
  reward: 'Theater trip',
  hasRule: true,
  startDate: '2026-10-16',
  targetDays: 7,
  periodStart: '2026-10-12',
  periodEnd: '2026-10-22',
  periodLabel: 'Week of Oct 12',
  overdue: false,
  status: 'active',
  rating: null,
  review: null,
  endedAt: null,
  earnedAt: null,
  redeemedAt: null,
  lastCheckedAt: null,
  currentStreak: 6,
  bestStreak: 6,
  needsReview: false,
  days: [],
};

function current(week: Goal[], overarching: Goal | null = null): CurrentGoals {
  const period = (
    horizon: 'week' | 'month' | 'quarter' | 'year',
    label: string,
    goals: Goal[]
  ) => ({
    horizon,
    label,
    periodStart: '2026-10-12',
    periodEnd: '2026-10-18',
    goals,
    unset: goals.length === 0,
  });
  return {
    today: '2026-10-16',
    overarching,
    horizons: [
      period('week', 'Week of Oct 12', week),
      period('month', 'October 2026', []),
      period('quarter', 'Q4 2026', []),
      period('year', '2026', []),
    ],
  };
}

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

it('checks a rule goal on demand and shows the newly earned prize', async () => {
  vi.mocked(api.goals.current).mockResolvedValue(current([base]));
  const earned: Goal = {
    ...base,
    status: 'earned',
    currentStreak: 7,
    bestStreak: 7,
    earnedAt: '2026-10-22T12:00:00Z',
  };
  vi.mocked(api.goals.check).mockImplementation(async () => {
    vi.mocked(api.goals.current).mockResolvedValue(current([earned]));
    return earned;
  });
  mount();
  fireEvent.click(await screen.findByRole('button', { name: 'Check now' }));
  await screen.findByText('7/7 consecutive days · Reward earned');
  expect(api.goals.check).toHaveBeenCalledWith(base.id);
  fireEvent.click(screen.getByRole('button', { name: 'Mark prize redeemed' }));
  await waitFor(() =>
    expect(api.goals.update).toHaveBeenCalledWith(base.id, 'redeem')
  );
});

it('opens cited journal entries and distinguishes unresolved evidence', async () => {
  vi.mocked(api.goals.current).mockResolvedValue(
    current([
      {
        ...base,
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
    ])
  );
  const open = mount();
  await screen.findByText('1 day(s) need clearer evidence or another check.');
  fireEvent.click(screen.getByText('Daily evidence'));
  fireEvent.click(screen.getByRole('button', { name: 'Open journal entry' }));
  expect(open).toHaveBeenCalledWith('entry-one');
  expect(
    screen.queryByRole('button', { name: 'Mark prize redeemed' })
  ).toBeNull();
});

it('shows a plain goal without streak controls and flags unset periods', async () => {
  vi.mocked(api.goals.current).mockResolvedValue(
    current([
      {
        ...base,
        criteria: null,
        reward: null,
        hasRule: false,
        targetDays: null,
        details: 'Two chapters',
      },
    ])
  );
  mount();
  await screen.findByText('Two chapters');
  expect(screen.queryByRole('button', { name: 'Check now' })).toBeNull();
  expect(screen.getByText('No goals for October 2026 yet.')).toBeTruthy();
  expect(screen.getByText('No overarching goal yet.')).toBeTruthy();
});

it('adds a goal to a horizon and changes the overarching goal', async () => {
  const over = {
    ...base,
    id: 'o',
    horizon: 'overarching' as const,
    hasRule: false,
    details: 'Be well.',
  };
  vi.mocked(api.goals.current).mockResolvedValue(current([], over));
  mount();
  fireEvent.change(await screen.findByLabelText('New goal for this month'), {
    target: { value: 'Cook at home' },
  });
  fireEvent.click(screen.getAllByRole('button', { name: 'Add' })[1]);
  await waitFor(() =>
    expect(api.goals.create).toHaveBeenCalledWith({
      horizon: 'month',
      title: 'Cook at home',
    })
  );
  fireEvent.click(screen.getByRole('button', { name: 'Change' }));
  fireEvent.change(screen.getByLabelText('Overarching goal'), {
    target: { value: 'Be well and strong.' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await waitFor(() =>
    expect(api.goals.setOverarching).toHaveBeenCalledWith('Be well and strong.')
  );
});

it('shows check failures without claiming the reward was earned', async () => {
  vi.mocked(api.goals.current).mockResolvedValue(current([base]));
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
