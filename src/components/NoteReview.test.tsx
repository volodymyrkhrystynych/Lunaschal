// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, NoteToSelf } from '../hooks/api';
import { NoteReviewButton } from './NoteReview';

vi.mock('../hooks/api', () => ({
  api: {
    notes: {
      due: vi.fn(),
      dismiss: vi.fn(),
      update: vi.fn(),
      revisions: vi.fn().mockResolvedValue([]),
    },
  },
}));

const note = (overrides: Partial<NoteToSelf> = {}): NoteToSelf => ({
  id: 'n1',
  content: 'water the plants',
  intervalDays: 1,
  due: '2026-08-11T00:00:00Z',
  createdAt: '2026-08-10T14:20:00',
  updatedAt: '2026-08-10T14:20:00',
  ...overrides,
});

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.notes.revisions).mockResolvedValue([]);
});

function renderButton() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <NoteReviewButton />
    </QueryClientProvider>
  );
}

describe('the review button', () => {
  it('is disabled with no badge when nothing is due', async () => {
    vi.mocked(api.notes.due).mockResolvedValue([]);
    renderButton();

    const button = (await screen.findByRole('button', {
      name: 'Review',
    })) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

  it('shows a due count and is clickable when notes are due', async () => {
    vi.mocked(api.notes.due).mockResolvedValue([note()]);
    renderButton();

    const button = (await screen.findByRole('button', {
      name: 'Review (1 due)',
    })) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
  });
});

describe('the review modal', () => {
  it('shows the soonest-due note with when it was created', async () => {
    vi.mocked(api.notes.due).mockResolvedValue([note()]);
    renderButton();

    fireEvent.click(
      await screen.findByRole('button', { name: 'Review (1 due)' })
    );

    expect(await screen.findByText('water the plants')).toBeTruthy();
    expect(screen.getByText(/Created/)).toBeTruthy();
  });

  it('dismisses the current note, which advances it off the due list', async () => {
    vi.mocked(api.notes.due).mockResolvedValueOnce([note()]);
    vi.mocked(api.notes.dismiss).mockResolvedValue(note({ intervalDays: 2 }));
    renderButton();

    fireEvent.click(
      await screen.findByRole('button', { name: 'Review (1 due)' })
    );
    await screen.findByText('water the plants');

    vi.mocked(api.notes.due).mockResolvedValueOnce([]);
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));

    await waitFor(() => expect(api.notes.dismiss).toHaveBeenCalledWith('n1'));
    await waitFor(() => expect(screen.getByText(/All caught up/)).toBeTruthy());
  });

  it('edits the note content and saves it', async () => {
    vi.mocked(api.notes.due).mockResolvedValue([note()]);
    vi.mocked(api.notes.update).mockResolvedValue(
      note({ content: 'water the plants twice a week' })
    );
    renderButton();

    fireEvent.click(
      await screen.findByRole('button', { name: 'Review (1 due)' })
    );
    await screen.findByText('water the plants');

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    const textarea = screen.getByRole('textbox');
    fireEvent.change(textarea, {
      target: { value: 'water the plants twice a week' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(api.notes.update).toHaveBeenCalledWith(
        'n1',
        'water the plants twice a week'
      )
    );
  });

  it('shows edit history when the note has revisions', async () => {
    vi.mocked(api.notes.due).mockResolvedValue([note()]);
    vi.mocked(api.notes.revisions).mockResolvedValue([
      {
        id: 'r1',
        noteId: 'n1',
        content: 'water the plants weekly',
        createdAt: '2026-08-09T10:00:00',
      },
    ]);
    renderButton();

    fireEvent.click(
      await screen.findByRole('button', { name: 'Review (1 due)' })
    );
    await screen.findByText('water the plants');

    const historyToggle = await screen.findByText('Show edit history (1)');
    fireEvent.click(historyToggle);

    expect(await screen.findByText('water the plants weekly')).toBeTruthy();
  });
});
