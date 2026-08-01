// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, type ProgressionPoint } from '@/hooks/api';
import { Progression } from './Progression';

vi.mock('@/hooks/api', () => ({
  api: {
    lifestyle: {
      weight: { list: vi.fn().mockResolvedValue([]), log: vi.fn() },
      exercises: {
        list: vi.fn().mockResolvedValue([]),
        progression: vi.fn(),
      },
    },
  },
}));

const exercises = vi.mocked(api.lifestyle.exercises.list);
const progression = vi.mocked(api.lifestyle.exercises.progression);

const point = (
  date: string,
  over: Partial<ProgressionPoint> = {}
): ProgressionPoint => ({
  date,
  maxWeight: null,
  totalVolume: null,
  totalReps: null,
  setCount: 0,
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
  exercises.mockResolvedValue([
    { name: 'squat', displayName: 'Squat', sessionCount: 2, lastDate: null },
  ]);
});

function renderProgression() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <Progression />
    </QueryClientProvider>
  );
}

describe('per-exercise chart', () => {
  it('offers the weight/volume toggle for a loaded exercise', async () => {
    progression.mockResolvedValue({
      name: 'squat',
      displayName: 'Squat',
      points: [
        point('2026-07-01', { maxWeight: 65, totalVolume: 828, totalReps: 22 }),
        point('2026-07-08', { maxWeight: 70, totalVolume: 350, totalReps: 5 }),
      ],
    });
    renderProgression();

    expect(await screen.findByRole('button', { name: 'Top set' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Volume' })).toBeTruthy();
  });

  it('charts reps for a bodyweight exercise instead of an empty weight chart', async () => {
    // "squats 10 10 10 10" — no weight on any set, so maxWeight and volume are
    // null for every day. Plotting those as 0 would draw a flat line on the
    // floor; showing nothing would claim there was no training.
    progression.mockResolvedValue({
      name: 'squat',
      displayName: 'Squat',
      points: [
        point('2026-07-01', { totalReps: 30, setCount: 3 }),
        point('2026-07-08', { totalReps: 48, setCount: 4 }),
      ],
    });
    renderProgression();

    expect(await screen.findByText('Total reps (bodyweight)')).toBeTruthy();
    // A toggle whose two options can only ever be empty is not offered.
    expect(screen.queryByRole('button', { name: 'Top set' })).toBeNull();
    expect(screen.queryByText(/log a workout to start this chart/i)).toBeNull();
  });

  it('goes back to the weight chart once the exercise gets loaded', async () => {
    progression.mockResolvedValue({
      name: 'squat',
      displayName: 'Squat',
      points: [
        point('2026-07-01', { totalReps: 30 }),
        point('2026-07-08', { maxWeight: 20, totalVolume: 200, totalReps: 10 }),
      ],
    });
    renderProgression();

    expect(await screen.findByRole('button', { name: 'Top set' })).toBeTruthy();
    expect(screen.queryByText('Total reps (bodyweight)')).toBeNull();
  });

  it('still shows the empty state when nothing is logged at all', async () => {
    progression.mockResolvedValue({
      name: 'squat',
      displayName: 'Squat',
      points: [],
    });
    renderProgression();

    await waitFor(() => expect(progression).toHaveBeenCalled());
    expect(
      await screen.findByText(/log a workout to start this chart/i)
    ).toBeTruthy();
  });
});
