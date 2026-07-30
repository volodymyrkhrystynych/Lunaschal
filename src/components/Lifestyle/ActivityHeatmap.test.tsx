// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, type HeatmapDayResponse } from '@/hooks/api';
import { ACTIVITY_LABELS, todayISO } from '@/lib/lifestyle';
import { ActivityHeatmap } from './ActivityHeatmap';

vi.mock('@/hooks/api', () => ({
  api: { lifestyle: { heatmap: vi.fn() } },
}));

const heatmap = vi.mocked(api.lifestyle.heatmap);
const TODAY = todayISO();

const dayResponse = (
  over: Partial<HeatmapDayResponse> = {}
): HeatmapDayResponse => ({
  date: TODAY,
  activityType: 'goodlife_brother',
  secondary: false,
  durationMinutes: 60,
  intensityRating: 7,
  sessions: [],
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
  heatmap.mockResolvedValue([]);
});

function renderHeatmap() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ActivityHeatmap />
    </QueryClientProvider>
  );
}

describe('legend', () => {
  it('names all four activity types, so colour never carries identity alone', async () => {
    renderHeatmap();
    await waitFor(() => expect(heatmap).toHaveBeenCalled());
    for (const label of Object.values(ACTIVITY_LABELS)) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
    expect(screen.getByText(/second activity that day/i)).toBeTruthy();
  });
});

describe('day cells', () => {
  it('describes a logged day in its accessible label', async () => {
    heatmap.mockResolvedValue([
      dayResponse({ durationMinutes: 90, intensityRating: 8 }),
    ]);
    renderHeatmap();

    const cell = await screen.findByLabelText(
      new RegExp(`${TODAY}: Goodlife with brother, 90 min, RPE 8`)
    );
    expect(cell).toBeTruthy();
  });

  it('calls out a second activity in the label as well as the corner mark', async () => {
    heatmap.mockResolvedValue([dayResponse({ secondary: true })]);
    renderHeatmap();

    expect(
      await screen.findByLabelText(
        new RegExp(`${TODAY}.*plus another activity`)
      )
    ).toBeTruthy();
  });

  it('marks a day with nothing logged, and leaves it unclickable', async () => {
    renderHeatmap();
    const cell = await screen.findByLabelText(`${TODAY}: nothing logged`);
    expect((cell as HTMLButtonElement).disabled).toBe(true);
  });

  it('opens that day’s sessions when clicked, and closes on a second click', async () => {
    heatmap.mockResolvedValue([
      dayResponse({
        sessions: [
          {
            id: 's1',
            date: TODAY,
            locationType: 'goodlife_brother',
            durationMinutes: 60,
            intensityRating: 7,
            rawText: 'squats 60,8',
            notes: null,
            parseStatus: 'done',
            exercises: [
              {
                id: 'e1',
                nameRaw: 'squats',
                nameCanonical: 'squat',
                displayName: 'Squat',
                position: 0,
                sets: [{ id: 'st1', weight: 60, reps: 8, setOrder: 0 }],
              },
            ],
            createdAt: '',
            updatedAt: '',
          },
        ],
      }),
    ]);
    renderHeatmap();

    const cell = await screen.findByLabelText(new RegExp(`^${TODAY}:`));
    fireEvent.click(cell);
    await waitFor(() =>
      expect(screen.getByText(/Squat \(1 sets\)/)).toBeTruthy()
    );
    expect(screen.getByText(/60 min · RPE 7/)).toBeTruthy();

    fireEvent.click(cell);
    await waitFor(() =>
      expect(screen.queryByText(/Squat \(1 sets\)/)).toBeNull()
    );
  });
});

describe('shade metric toggle', () => {
  it('offers duration and intensity, defaulting to duration', async () => {
    renderHeatmap();
    await waitFor(() => expect(heatmap).toHaveBeenCalled());
    const duration = screen.getByRole('button', { name: 'Duration' });
    const intensity = screen.getByRole('button', { name: 'Intensity' });
    // The active toggle is the one carrying the primary background.
    expect(duration.className).toContain('bg-[var(--color-primary)]');
    expect(intensity.className).not.toContain('bg-[var(--color-primary)]');

    fireEvent.click(intensity);
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Intensity' }).className
      ).toContain('bg-[var(--color-primary)]')
    );
  });
});
