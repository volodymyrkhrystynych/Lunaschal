// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { PianoToday } from './PianoToday';

const { today, history, completeExercise } = vi.hoisted(() => ({
  today: vi.fn(),
  history: vi.fn(),
  completeExercise: vi.fn(),
}));

vi.mock('../../hooks/api', () => ({
  api: {
    piano: {
      today,
      history,
      completeExercise,
      updatePreferences: vi.fn(),
    },
  },
}));

describe('PianoToday', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    today.mockResolvedValue({
      dayKey: '2026-09-02',
      preferences: {
        sessionMinutes: 25,
        skillLevel: 'intermediate',
        jazzPercent: 50,
        updatedAt: '2026-09-02T12:00:00+00:00',
      },
      exercises: [
        {
          id: 'daily-1',
          exerciseKey: 'ear-phrase',
          title: 'Learn a phrase by ear',
          category: 'Ear',
          style: 'shared',
          description: 'Listen closely.',
          instructions: 'Sing it, then play it.',
          keyName: 'C',
          targetTempo: null,
          minutes: 5,
          gradeable: false,
          pianoPieceId: null,
          measureStart: null,
          measureEnd: null,
          completedAt: null,
          latestAttempt: null,
        },
      ],
    });
    history.mockResolvedValue([
      {
        dayKey: '2026-09-02',
        exerciseCount: 1,
        completedCount: 0,
        minutesPlanned: 25,
      },
    ]);
    completeExercise.mockResolvedValue({ id: 'attempt-1' });
  });

  it('shows the daily routine and persists a creative self-rating', async () => {
    render(<PianoToday onPractice={vi.fn()} onRepertoire={vi.fn()} />);

    expect(await screen.findByText('Learn a phrase by ear')).toBeTruthy();
    fireEvent.change(
      screen.getByLabelText('Rating for Learn a phrase by ear'),
      {
        target: { value: '4' },
      }
    );
    fireEvent.click(screen.getByRole('button', { name: 'Mark complete' }));

    await waitFor(() =>
      expect(completeExercise).toHaveBeenCalledWith('daily-1', {
        selfRating: 4,
      })
    );
  });
});
