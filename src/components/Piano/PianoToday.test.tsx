// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { PianoDailyExercise } from '../../lib/piano';
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

function exercise(
  overrides: Partial<PianoDailyExercise> & { id: string }
): PianoDailyExercise {
  return {
    exerciseKey: 'five-finger',
    title: 'Five-finger warm-up',
    category: 'Warm-up',
    style: 'shared',
    description: 'Relaxed, even fingers.',
    instructions: 'Play slowly.',
    group: 'keys',
    keyName: 'C',
    targetTempo: 80,
    minutes: 5,
    gradeable: true,
    pianoPieceId: null,
    measureStart: null,
    measureEnd: null,
    completedAt: null,
    cleanStreak: 0,
    practicedSeconds: 0,
    latestAttempt: null,
    ...overrides,
  };
}

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
        exercise({ id: 'keys-1', cleanStreak: 2, practicedSeconds: 190 }),
        exercise({
          id: 'keys-2',
          exerciseKey: 'scales',
          title: 'Scale and arpeggio',
          minutes: 6,
        }),
        exercise({
          id: 'ear-1',
          exerciseKey: 'ear-phrase',
          title: 'Learn a phrase by ear',
          category: 'Ear',
          group: 'ear',
          targetTempo: null,
        }),
        exercise({
          id: 'free-1',
          exerciseKey: 'comping',
          title: 'Comping and time',
          category: 'Rhythm',
          group: 'freeform',
          gradeable: false,
          targetTempo: null,
        }),
        exercise({
          id: 'rep-1',
          exerciseKey: 'repertoire',
          title: 'Repertoire focus',
          category: 'Repertoire',
          group: 'repertoire',
          keyName: null,
          targetTempo: null,
          pieceTitle: 'Gymnopédie No. 1',
        }),
      ],
    });
    history.mockResolvedValue([
      {
        dayKey: '2026-09-02',
        exerciseCount: 5,
        completedCount: 0,
        minutesPlanned: 25,
      },
    ]);
    completeExercise.mockResolvedValue({ id: 'attempt-1' });
  });

  const renderToday = (onStartDrill = vi.fn()) => {
    render(
      <PianoToday
        onPractice={vi.fn()}
        onRepertoire={vi.fn()}
        onStartDrill={onStartDrill}
      />
    );
    return onStartDrill;
  };

  it('splits the routine into practice blocks and repertoire', async () => {
    renderToday();

    expect(await screen.findByText('Practice')).toBeTruthy();
    expect(screen.getByText('Repertoire')).toBeTruthy();
    // The keys exercises are one block with one button, not a card each.
    expect(screen.getByText('Warm-up, technique and harmony')).toBeTruthy();
    // The block has time on it already, so the one button offers to resume.
    expect(
      screen.getAllByRole('button', { name: 'Resume practice' })
    ).toHaveLength(1);
    expect(
      screen.getByRole('button', { name: 'Listen and play back' })
    ).toBeTruthy();
    expect(
      screen.getByRole('button', { name: 'Open Gymnopédie No. 1' })
    ).toBeTruthy();
  });

  it('shows each keys exercise’s clean-run streak and time spent', async () => {
    renderToday();

    expect(
      await screen.findByLabelText('Five-finger warm-up: 2 of 3 clean runs')
    ).toBeTruthy();
    expect(screen.getByText('3 / 5 min')).toBeTruthy();
    expect(screen.getByText('0 / 6 min')).toBeTruthy();
  });

  it('hands the whole routine to the drill, which picks its own queue', async () => {
    today.mockResolvedValue({
      dayKey: '2026-09-02',
      preferences: {
        sessionMinutes: 25,
        skillLevel: 'intermediate',
        jazzPercent: 50,
        updatedAt: '2026-09-02T12:00:00+00:00',
      },
      exercises: [
        exercise({ id: 'keys-1' }),
        exercise({ id: 'rep-1', group: 'repertoire', pieceTitle: 'Etude' }),
      ],
    });
    const onStartDrill = renderToday();

    fireEvent.click(
      await screen.findByRole('button', { name: 'Start practice' })
    );
    expect(onStartDrill).toHaveBeenCalledWith([
      expect.objectContaining({ id: 'keys-1' }),
      expect.objectContaining({ id: 'rep-1' }),
    ]);
  });

  it('persists a self-rating for the exercises away from the screen', async () => {
    renderToday();

    expect(await screen.findByText('Comping and time')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Rating for Comping and time'), {
      target: { value: '4' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Mark complete' }));

    await waitFor(() =>
      expect(completeExercise).toHaveBeenCalledWith('free-1', {
        selfRating: 4,
      })
    );
  });
});
