// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, type HealthActivity } from '@/hooks/api';
import { HealthCard } from './HealthCard';

vi.mock('@/hooks/api', () => ({
  api: { lifestyle: { health: vi.fn() } },
}));

const health = vi.mocked(api.lifestyle.health);

function renderCard() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <HealthCard />
    </QueryClientProvider>
  );
}

const days = (minutes: (number | null)[]): HealthActivity['days'] =>
  minutes.map((m, i) => ({
    date: `2026-10-0${i + 1}`,
    exerciseMinutes: m,
    steps: m === null ? null : 5000 + i,
    activeEnergyKcal: m === null ? null : 300,
  }));

describe('HealthCard', () => {
  it('says how to connect when the phone has never synced', async () => {
    health.mockResolvedValue({ days: [], workouts: [], lastSyncedAt: null });
    renderCard();
    expect(
      await screen.findByText(/Nothing from Apple Health yet/)
    ).toBeTruthy();
  });

  it("shows today's totals, the month's minutes and the Watch's workouts", async () => {
    health.mockResolvedValue({
      days: days([20, null, 41]),
      workouts: [
        {
          id: 'w1',
          activityType: 37,
          activityName: 'running',
          start: Date.now() / 1000 - 3600,
          end: Date.now() / 1000,
          durationSeconds: 1860,
          energyKcal: 340,
          distanceMeters: 5200,
          source: 'Apple Watch',
        },
      ],
      lastSyncedAt: Date.now() / 1000 - 300,
    });
    renderCard();
    expect(await screen.findByText('41 min')).toBeTruthy();
    expect(screen.getByText('5,002')).toBeTruthy();
    expect(screen.getByText('61 min · 31/day')).toBeTruthy();
    expect(screen.getByText('Running')).toBeTruthy();
    expect(screen.getByText('31 min · 5.2 km · 340 kcal')).toBeTruthy();
    expect(screen.getByText('Synced 5 min ago')).toBeTruthy();
  });
});
