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

  it('sends duration and RPE as numbers, and omits blanks as null', async () => {
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
