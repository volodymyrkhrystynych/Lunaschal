// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api } from '@/hooks/api';
import {
  loadWorkoutDraft,
  saveWorkoutDraft,
  EMPTY_DRAFT,
} from '@/lib/workoutDraft';
import { WorkoutLog } from './WorkoutLog';

vi.mock('@/hooks/api', () => ({
  api: {
    lifestyle: {
      workouts: {
        list: vi.fn().mockResolvedValue([]),
        create: vi.fn().mockResolvedValue({ id: 'w1' }),
        reparse: vi.fn(),
        delete: vi.fn(),
      },
    },
  },
}));

const create = vi.mocked(api.lifestyle.workouts.create);

beforeEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
  localStorage.clear();
});

function renderLog() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <WorkoutLog />
    </QueryClientProvider>
  );
}

// This repo has no jest-dom, so assertions read the DOM properties directly.
const textarea = () =>
  screen.getByPlaceholderText(/bicep curls/i) as HTMLTextAreaElement;
const durationInput = () =>
  screen.getByLabelText(/duration/i) as HTMLInputElement;
const logButton = () =>
  screen.getByRole('button', { name: /log workout/i }) as HTMLButtonElement;

describe('mid-workout draft persistence', () => {
  it('restores what was typed before a reload wiped the tab', async () => {
    // A phone reloading a backgrounded tab re-mounts the component from
    // scratch — the textarea must come back filled, not empty.
    saveWorkoutDraft({
      ...EMPTY_DRAFT,
      rawText: 'bicep curls 20,10',
      locationType: 'goodlife_brother',
      durationMinutes: '45',
    });

    renderLog();

    expect(textarea().value).toBe('bicep curls 20,10');
    expect(durationInput().value).toBe('45');
    expect(
      screen
        .getByRole('button', { name: /goodlife with brother/i })
        .getAttribute('aria-pressed')
    ).toBe('true');
    expect(screen.getByText(/draft restored/i)).toBeTruthy();
  });

  it('mirrors typing into localStorage on a debounce', async () => {
    renderLog();
    fireEvent.change(textarea(), { target: { value: 'squats 60,8' } });

    await waitFor(() =>
      expect(loadWorkoutDraft()?.rawText).toBe('squats 60,8')
    );
  });

  it('starts empty and shows no restore notice with nothing stored', () => {
    renderLog();
    expect(textarea().value).toBe('');
    expect(screen.queryByText(/draft restored/i)).toBeNull();
  });

  it('keeps the draft when saving fails, so nothing is lost', async () => {
    create.mockRejectedValueOnce(new Error('offline'));
    renderLog();

    fireEvent.change(textarea(), { target: { value: 'squats 60,8' } });
    fireEvent.click(screen.getByRole('button', { name: /goodlife alone/i }));
    fireEvent.click(logButton());

    await waitFor(() => expect(screen.getByText('offline')).toBeTruthy());
    expect(textarea().value).toBe('squats 60,8');
    await waitFor(() =>
      expect(loadWorkoutDraft()?.rawText).toBe('squats 60,8')
    );
  });

  it('clears the draft only once the server has the session', async () => {
    renderLog();

    fireEvent.change(textarea(), { target: { value: 'squats 60,8' } });
    fireEvent.click(screen.getByRole('button', { name: /goodlife alone/i }));
    fireEvent.click(logButton());

    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create).toHaveBeenCalledWith(
      expect.objectContaining({
        locationType: 'goodlife_alone',
        rawText: 'squats 60,8',
      })
    );
    await waitFor(() => expect(textarea().value).toBe(''));
    expect(loadWorkoutDraft()).toBeNull();
  });
});

describe('submission guards', () => {
  it('refuses to submit without an activity type', async () => {
    renderLog();
    fireEvent.change(textarea(), { target: { value: 'squats 60,8' } });
    fireEvent.click(logButton());

    await waitFor(() =>
      expect(screen.getByText(/pick where you trained/i)).toBeTruthy()
    );
    expect(create).not.toHaveBeenCalled();
  });

  it('disables the button while the form is empty', () => {
    renderLog();
    expect(logButton().disabled).toBe(true);
  });

  it('sends duration and intensity as numbers, and omits blanks as null', async () => {
    renderLog();
    fireEvent.click(screen.getByRole('button', { name: /outside/i }));
    fireEvent.change(durationInput(), { target: { value: '45' } });
    fireEvent.click(logButton());

    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create).toHaveBeenCalledWith(
      expect.objectContaining({
        durationMinutes: 45,
        intensityRating: null,
      })
    );
  });
});

describe('intensity stars', () => {
  const star = (n: number) =>
    screen.getByRole('radio', { name: new RegExp(`^${n} of 5`) });

  it('offers five stars, each naming what it means', () => {
    renderLog();
    // The written meanings are the reason the 1-10 RPE was replaced, so they
    // have to be reachable — as the accessible name and as a tooltip.
    const meanings = [
      'Not intense whatsoever',
      'Just a smidge',
      "I'm sweating",
      "I'm really trying hard",
      'I am going ham',
    ];
    meanings.forEach((meaning, i) => {
      const button = star(i + 1);
      expect(button.getAttribute('aria-label')).toContain(meaning);
      expect(button.getAttribute('title')).toContain(meaning);
    });
    expect(screen.getAllByRole('radio')).toHaveLength(5);
  });

  it('shows the picked meaning and sends the star count', async () => {
    renderLog();
    fireEvent.click(screen.getByRole('button', { name: /outside/i }));
    fireEvent.click(star(4));

    expect(screen.getByText("I'm really trying hard")).toBeTruthy();
    expect(star(4).getAttribute('aria-checked')).toBe('true');
    expect(star(5).getAttribute('aria-checked')).toBe('false');

    fireEvent.click(logButton());
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create).toHaveBeenCalledWith(
      expect.objectContaining({ intensityRating: 4 })
    );
  });

  it('clears the rating when the picked star is tapped again', () => {
    renderLog();
    fireEvent.click(star(3));
    expect(screen.getByText("I'm sweating")).toBeTruthy();

    fireEvent.click(star(3));
    expect(star(3).getAttribute('aria-checked')).toBe('false');
    expect(screen.getByText('Not rated')).toBeTruthy();
  });

  it('keeps the rating in the localStorage draft', async () => {
    renderLog();
    fireEvent.click(star(5));
    await waitFor(() => expect(loadWorkoutDraft()?.intensityRating).toBe('5'));
  });
});

describe('session history', () => {
  it('renders bodyweight sets as bodyweight, never as a zero weight', async () => {
    // "squats 10 10 10 10" — four sets of ten, nothing loaded.
    vi.mocked(api.lifestyle.workouts.list).mockResolvedValue([
      {
        id: 's1',
        date: '2026-07-20',
        locationType: 'outside',
        durationMinutes: 30,
        intensityRating: 2,
        rawText: 'squats 10 10 10 10',
        notes: null,
        parseStatus: 'done',
        exercises: [
          {
            id: 'e1',
            nameRaw: 'squats',
            nameCanonical: 'squat',
            displayName: 'Squat',
            position: 0,
            sets: Array.from({ length: 4 }, (_, i) => ({
              id: `st${i}`,
              weight: null,
              reps: 10,
              setOrder: i,
            })),
          },
        ],
        createdAt: '',
        updatedAt: '',
      },
    ]);
    renderLog();

    expect(await screen.findByText('10 × 4 bodyweight')).toBeTruthy();
    expect(screen.queryByText(/0×10/)).toBeNull();
    // Intensity reads out in words alongside the stars.
    expect(screen.getByText('Intensity 2/5 — Just a smidge')).toBeTruthy();
  });
});

describe('delete protection', () => {
  const session = {
    id: 's1',
    date: '2026-07-20',
    locationType: 'outside' as const,
    durationMinutes: 30,
    intensityRating: 2,
    rawText: 'squats 10 10 10 10',
    notes: null,
    parseStatus: 'done' as const,
    exercises: [],
    createdAt: '',
    updatedAt: '',
  };

  it('hides the delete button until "Show delete buttons" is toggled', async () => {
    vi.mocked(api.lifestyle.workouts.list).mockResolvedValue([session]);
    renderLog();

    await screen.findByText(/2026-07-20/);
    expect(screen.queryByRole('button', { name: /^delete$/i })).toBeNull();

    fireEvent.click(screen.getByTitle('Show delete buttons'));
    expect(screen.getByRole('button', { name: /^delete$/i })).toBeTruthy();
  });

  it('only deletes once shown, and never on its own', async () => {
    vi.mocked(api.lifestyle.workouts.list).mockResolvedValue([session]);
    renderLog();

    await screen.findByText(/2026-07-20/);
    fireEvent.click(screen.getByTitle('Show delete buttons'));
    fireEvent.click(screen.getByRole('button', { name: /^delete$/i }));

    await waitFor(() =>
      expect(api.lifestyle.workouts.delete).toHaveBeenCalledWith('s1')
    );
  });
});
