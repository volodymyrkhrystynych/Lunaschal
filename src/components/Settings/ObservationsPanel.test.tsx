// @vitest-environment jsdom
/**
 * The delete button here is the whole reason `remember` is allowed to write
 * without a confirmation card. Its predecessor wrote the user's own document
 * silently and was removed for it; what makes this version a fair trade is that
 * every note it writes is visible and one click from gone.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { ObservationsPanel } from './ObservationsPanel';

vi.mock('../../hooks/api', () => ({
  api: { memory: { observations: vi.fn(), deleteObservation: vi.fn() } },
}));

const renderPanel = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ObservationsPanel />
    </QueryClientProvider>
  );

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.memory.observations).mockResolvedValue({
    observations: [
      {
        id: 'obs-1',
        content: 'Trains on Tuesdays and Fridays',
        source: 'chat',
        createdAt: '2026-03-04T10:00:00',
        foldedAt: null,
      },
    ],
    maxPending: 40,
  });
  vi.mocked(api.memory.deleteObservation).mockResolvedValue({ ok: true });
});

describe('ObservationsPanel', () => {
  it('shows what the assistant has noted', async () => {
    renderPanel();
    expect(
      await screen.findByText('Trains on Tuesdays and Fridays')
    ).toBeTruthy();
  });

  it('deletes a note and refetches', async () => {
    renderPanel();
    fireEvent.click(
      await screen.findByLabelText(
        'Delete note: Trains on Tuesdays and Fridays'
      )
    );
    await waitFor(() =>
      expect(api.memory.deleteObservation).toHaveBeenCalledWith('obs-1')
    );
    await waitFor(() =>
      expect(api.memory.observations).toHaveBeenCalledTimes(2)
    );
  });

  it('says so plainly when nothing has been noted', async () => {
    vi.mocked(api.memory.observations).mockResolvedValue({
      observations: [],
      maxPending: 40,
    });
    renderPanel();
    expect(await screen.findByText('Nothing noted yet.')).toBeTruthy();
  });

  it('shows how full the queue is, since the assistant stops at the cap', async () => {
    renderPanel();
    expect(await screen.findByText('1 / 40')).toBeTruthy();
  });
});
