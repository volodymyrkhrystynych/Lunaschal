// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, type GamingStatus } from '../../hooks/api';
import { GamingLockSection } from './GamingLockSection';

vi.mock('../../hooks/api', () => ({
  api: {
    gaming: { status: vi.fn(), updateSettings: vi.fn(), override: vi.fn() },
  },
}));

const locked: GamingStatus = {
  day: '2026-11-11',
  applies: true,
  locked: true,
  unlockedAt: null,
  method: null,
  reason: null,
  ratingAtUnlock: null,
  workEnd: null,
  settings: { enabled: true, days: [0, 1, 2, 3, 4], patterns: ['steam'] },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.gaming.status).mockResolvedValue(locked);
});

function renderSection() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <GamingLockSection />
    </QueryClientProvider>
  );
}

describe('GamingLockSection', () => {
  it('toggles a weekday', async () => {
    vi.mocked(api.gaming.updateSettings).mockResolvedValue(locked);
    renderSection();
    fireEvent.click(await screen.findByRole('button', { name: 'Sat' }));
    await waitFor(() =>
      expect(api.gaming.updateSettings).toHaveBeenCalledWith({
        days: [0, 1, 2, 3, 4, 5],
      })
    );
  });

  it('only overrides with a real reason', async () => {
    vi.mocked(api.gaming.override).mockResolvedValue({
      ...locked,
      locked: false,
      method: 'override',
    });
    renderSection();
    fireEvent.click(await screen.findByText('Unlock without the review…'));
    const unlock = screen.getByRole('button', { name: 'Unlock anyway' });
    fireEvent.change(screen.getByLabelText('Why unlock without the review'), {
      target: { value: 'bored' },
    });
    expect((unlock as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('Why unlock without the review'), {
      target: { value: 'Friends online for a raid' },
    });
    fireEvent.click(unlock);
    await waitFor(() =>
      expect(api.gaming.override).toHaveBeenCalledWith(
        'Friends online for a raid',
        true
      )
    );
    await waitFor(() =>
      expect(screen.getByTestId('gaming-status').textContent).toBe(
        'Unlocked without the review'
      )
    );
  });
});
