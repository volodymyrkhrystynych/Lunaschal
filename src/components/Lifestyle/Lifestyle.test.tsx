// @vitest-environment jsdom
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
} from '@tanstack/react-query';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import {
  api,
  type BodyWeightLog,
  type CalorieDay,
  type Selfie,
} from '@/hooks/api';
import { todayISO } from '@/lib/lifestyle';
import { Lifestyle } from './Lifestyle';

vi.mock('../Tasks', () => ({ TasksSection: () => <div>Tasks</div> }));
vi.mock('./ActivityHeatmap', () => ({
  ActivityHeatmap: () => <div>Activity</div>,
}));
vi.mock('./TrendsChart', () => ({ TrendsChart: () => <div>Trends</div> }));
vi.mock('./WorkoutLog', () => ({ WorkoutLog: () => <div>Workout</div> }));
vi.mock('./WeatherCard', () => ({ WeatherCard: () => <div>Weather</div> }));
vi.mock('./SelfieCard', () => ({ SelfieCard: () => <div>Selfie</div> }));
vi.mock('./CaloriesCard', () => ({
  CaloriesCard: () => {
    const { data } = useQuery({
      queryKey: ['lifestyle', 'calories'],
      queryFn: () => api.lifestyle.calories.day(),
    });
    return (
      <>
        <label>
          Calories
          <input aria-label="Calorie entry" />
        </label>
        <span>Total: {data?.total}</span>
      </>
    );
  },
}));
vi.mock('./Progression', () => ({
  BodyWeightCard: () => <div>Body weight</div>,
  Progression: ({ hideBodyWeight }: { hideBodyWeight?: boolean }) => (
    <div>{hideBodyWeight ? 'Progression without weight' : 'Progression'}</div>
  ),
}));

const today = todayISO();
const selfie = (date = today): Selfie => ({
  id: 'selfie-1',
  date,
  mime: 'image/jpeg',
  url: '/selfie.jpg',
  createdAt: `${date}T12:00:00Z`,
});
const weight = (date = today): BodyWeightLog => ({
  id: 'weight-1',
  date,
  weight: 80,
  createdAt: `${date}T12:00:00Z`,
  updatedAt: `${date}T12:00:00Z`,
});
const calorieDay = (total: number): CalorieDay => ({
  date: today,
  entries: [],
  total,
});

function wrapper(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  };
}

describe('Lifestyle daily priorities', () => {
  beforeEach(() => {
    vi.spyOn(api.lifestyle.selfies, 'list').mockResolvedValue([]);
    vi.spyOn(api.lifestyle.weight, 'list').mockResolvedValue([]);
    vi.spyOn(api.lifestyle.calories, 'day').mockResolvedValue(calorieDay(0));
  });

  it('puts unfinished selfie, weight, and calories above the normal layout', async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(<Lifestyle />, { wrapper: wrapper(client) });

    const priorities = await screen.findByLabelText("Today's priorities");
    expect(
      within(priorities)
        .getAllByText(/Selfie|Body weight|Calories/)
        .map(e => e.textContent)
    ).toEqual(['Selfie', 'Body weight', 'Calories']);
    expect(screen.getByText('Progression without weight')).toBeTruthy();
  });

  it('uses the normal layout when all three daily inputs are complete', async () => {
    vi.mocked(api.lifestyle.selfies.list).mockResolvedValue([selfie()]);
    vi.mocked(api.lifestyle.weight.list).mockResolvedValue([weight()]);
    vi.mocked(api.lifestyle.calories.day).mockResolvedValue(calorieDay(2000));
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(<Lifestyle />, { wrapper: wrapper(client) });

    await waitFor(() => {
      expect(api.lifestyle.selfies.list).toHaveBeenCalled();
      expect(api.lifestyle.weight.list).toHaveBeenCalled();
      expect(api.lifestyle.calories.day).toHaveBeenCalled();
    });
    expect(screen.queryByLabelText("Today's priorities")).toBeNull();
    expect(screen.getByText('Progression')).toBeTruthy();
    expect(screen.getByText('Calories')).toBeTruthy();
    expect(screen.getByText('Selfie')).toBeTruthy();
  });

  it('keeps the focused calorie input mounted when its shared cache completes', async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(<Lifestyle />, { wrapper: wrapper(client) });
    await screen.findByLabelText("Today's priorities");
    const input = screen.getByRole('textbox');
    input.focus();
    fireEvent.change(input, { target: { value: 'next meal 500' } });

    await act(async () => {
      client.setQueryData<Selfie[]>(['lifestyle', 'selfies'], [selfie()]);
      client.setQueryData<BodyWeightLog[]>(['lifestyle', 'weight'], [weight()]);
      client.setQueryData<CalorieDay>(
        ['lifestyle', 'calories'],
        calorieDay(2000)
      );
    });

    await screen.findByText('Total: 2000');
    expect(screen.getByRole('textbox')).toBe(input);
    expect(document.activeElement).toBe(input);
    expect(screen.getByDisplayValue('next meal 500')).toBe(input);
    expect(
      within(screen.getByLabelText("Today's priorities")).getByRole('textbox')
    ).toBe(input);
  });

  it('keeps the current layout if queries arrive after typing starts', async () => {
    let resolve!: (day: CalorieDay) => void;
    vi.mocked(api.lifestyle.calories.day).mockReturnValue(
      new Promise(r => {
        resolve = r;
      })
    );
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(<Lifestyle />, { wrapper: wrapper(client) });
    const input = screen.getByRole('textbox');
    act(() => input.focus());
    fireEvent.change(input, { target: { value: 'rice 600' } });
    await act(async () => {
      resolve(calorieDay(0));
    });
    await waitFor(() =>
      expect(client.getQueryData(['lifestyle', 'calories'])).toEqual(
        calorieDay(0)
      )
    );
    expect(screen.getByRole('textbox')).toBe(input);
    expect(document.activeElement).toBe(input);
    expect(screen.getByDisplayValue('rice 600')).toBe(input);
    expect(screen.queryByLabelText("Today's priorities")).toBeNull();
  });
});
