// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, type WorkoutSession } from '@/hooks/api';
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
        list: vi.fn(),
        recentExercises: vi.fn(),
        addEntry: vi.fn(),
        update: vi.fn(),
        reparse: vi.fn(),
        delete: vi.fn(),
      },
    },
  },
}));
const session: WorkoutSession = {
  id: 's1',
  date: '2026-09-20',
  locationType: 'unassigned',
  durationMinutes: 30,
  intensityRating: null,
  rawText: 'squats 10',
  notes: null,
  parseStatus: 'done',
  captureKind: 'strength',
  exercises: [
    {
      id: 'e1',
      nameRaw: 'squats',
      nameCanonical: 'squat',
      displayName: 'Squat',
      position: 0,
      sets: [{ id: 'set1', weight: null, reps: 10, setOrder: 0 }],
    },
  ],
  createdAt: '',
  updatedAt: '',
};
beforeEach(() => {
  vi.resetAllMocks();
  localStorage.clear();
  vi.mocked(api.lifestyle.workouts.list).mockResolvedValue([]);
  vi.mocked(api.lifestyle.workouts.recentExercises).mockResolvedValue([
    { name: 'bicep curl', displayName: 'Bicep Curl' },
  ]);
  vi.mocked(api.lifestyle.workouts.addEntry).mockResolvedValue({
    session,
    exercise: 'squat',
  });
  vi.mocked(api.lifestyle.workouts.update).mockResolvedValue({ success: true });
});
function renderLog() {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <WorkoutLog />
    </QueryClientProvider>
  );
}
const input = () =>
  screen.getByLabelText('Exercise entry') as HTMLTextAreaElement;
const enter = (value: string) => {
  fireEvent.change(input(), { target: { value } });
  fireEvent.keyDown(input(), { key: 'Enter' });
};

it('selects the most recent exercise and sends bare numbers with that context', async () => {
  renderLog();
  const pill = await screen.findByRole('button', { name: 'Bicep Curl' });
  expect(pill.getAttribute('aria-pressed')).toBe('true');
  enter('20, 10');
  await waitFor(() =>
    expect(api.lifestyle.workouts.addEntry).toHaveBeenCalledWith({
      text: '20, 10',
      exercise: 'bicep curl',
    })
  );
  await waitFor(() => expect(input().value).toBe(''));
});

it('uses an explicitly entered exercise for subsequent entries', async () => {
  vi.mocked(api.lifestyle.workouts.recentExercises).mockResolvedValue([]);
  renderLog();
  enter('squats 10');
  vi.mocked(api.lifestyle.workouts.recentExercises).mockResolvedValue([
    { name: 'squat', displayName: 'Squat' },
  ]);
  await waitFor(() => expect(input().value).toBe(''));
  expect(
    (await screen.findByRole('button', { name: 'Squat' })).getAttribute(
      'aria-pressed'
    )
  ).toBe('true');
  enter('12');
  await waitFor(() =>
    expect(api.lifestyle.workouts.addEntry).toHaveBeenLastCalledWith({
      text: '12',
      exercise: 'squat',
    })
  );
});

it('offers walking and cycling and saves duration with the selected activity', async () => {
  renderLog();
  fireEvent.click(screen.getByRole('button', { name: 'Walking' }));
  enter('30');
  await waitFor(() =>
    expect(api.lifestyle.workouts.addEntry).toHaveBeenCalledWith({
      text: '30',
      exercise: 'walking',
    })
  );
});

it('restores a numeric draft with its exercise selection', () => {
  saveWorkoutDraft({
    ...EMPTY_DRAFT,
    rawText: '20, 10',
    selectedExercise: 'bicep curl',
  });
  renderLog();
  expect(input().value).toBe('20, 10');
  expect(screen.getByText('Draft restored')).toBeTruthy();
});

it('flushes draft and selection immediately when the phone backgrounds', () => {
  renderLog();
  fireEvent.click(screen.getByRole('button', { name: 'Cycling' }));
  fireEvent.change(input(), { target: { value: '30' } });
  window.dispatchEvent(new Event('pagehide'));
  expect(loadWorkoutDraft()).toMatchObject({
    rawText: '30',
    selectedExercise: 'cycling',
  });
});

it('keeps the text after a failed save', async () => {
  vi.mocked(api.lifestyle.workouts.addEntry).mockRejectedValueOnce(
    new Error('offline')
  );
  renderLog();
  enter('squats 10');
  expect(await screen.findByRole('alert')).toBeTruthy();
  expect(input().value).toBe('squats 10');
  window.dispatchEvent(new Event('pagehide'));
  expect(loadWorkoutDraft()?.rawText).toBe('squats 10');
});

it('prevents repeated Enter while saving and preserves the saved draft until success', async () => {
  let finish!: (value: { session: WorkoutSession; exercise: string }) => void;
  vi.mocked(api.lifestyle.workouts.addEntry).mockImplementation(
    () =>
      new Promise(resolve => {
        finish = resolve;
      })
  );
  renderLog();
  enter('squats 10');
  fireEvent.keyDown(input(), { key: 'Enter' });
  await waitFor(() =>
    expect(api.lifestyle.workouts.addEntry).toHaveBeenCalledTimes(1)
  );
  expect(input().readOnly).toBe(true);
  finish({ session, exercise: 'squat' });
  await waitFor(() => expect(input().value).toBe(''));
  expect(loadWorkoutDraft()).toBeNull();
});

it('does not submit empty text, Shift+Enter, or composing Enter', () => {
  renderLog();
  fireEvent.keyDown(input(), { key: 'Enter' });
  fireEvent.change(input(), { target: { value: 'squat 10' } });
  fireEvent.keyDown(input(), { key: 'Enter', shiftKey: true });
  fireEvent.keyDown(input(), { key: 'Enter', isComposing: true });
  expect(api.lifestyle.workouts.addEntry).not.toHaveBeenCalled();
});

it('edits intensity and location on a saved workout', async () => {
  vi.mocked(api.lifestyle.workouts.list).mockResolvedValue([session]);
  renderLog();
  fireEvent.click(
    await screen.findByRole('button', { name: 'Rate / location' })
  );
  fireEvent.change(screen.getByLabelText('Location'), {
    target: { value: 'building' },
  });
  fireEvent.click(screen.getByRole('radio', { name: /^4 of 5/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Save details' }));
  await waitFor(() =>
    expect(api.lifestyle.workouts.update).toHaveBeenCalledWith('s1', {
      locationType: 'building',
      intensityRating: 4,
    })
  );
});

it('keeps outdoor location automatic while allowing rating', async () => {
  vi.mocked(api.lifestyle.workouts.list).mockResolvedValue([
    { ...session, captureKind: 'outdoor', locationType: 'outside' },
  ]);
  renderLog();
  fireEvent.click(
    await screen.findByRole('button', { name: 'Rate / location' })
  );
  expect(screen.queryByLabelText('Location')).toBeNull();
  expect(screen.getAllByRole('radio')).toHaveLength(5);
});

it('shows bodyweight history and protects deletion behind the toggle', async () => {
  vi.mocked(api.lifestyle.workouts.list).mockResolvedValue([session]);
  renderLog();
  expect(
    await screen.findByText(/bodyweight/, { selector: 'span' })
  ).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Delete' })).toBeNull();
  fireEvent.click(screen.getByTitle('Show delete buttons'));
  fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
  await waitFor(() =>
    expect(api.lifestyle.workouts.delete).toHaveBeenCalledWith('s1')
  );
});
