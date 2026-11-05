// @vitest-environment jsdom
import { expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, type SleepNight } from '@/hooks/api';
import { SleepCard } from './SleepCard';

vi.mock('@/hooks/api', () => ({ api: { lifestyle: { sleep: vi.fn() } } }));
const missing: SleepNight = {
  date: '2026-10-10',
  start: null,
  end: null,
  asleepMinutes: null,
  awakeMinutes: null,
  interruptions: null,
  efficiency: null,
  unobservedMinutes: null,
};
const recorded: SleepNight = {
  date: '2026-10-09',
  start: 1,
  end: 28801,
  asleepMinutes: 465,
  awakeMinutes: 15,
  interruptions: 1,
  efficiency: 96.9,
  unobservedMinutes: 0,
};

function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <SleepCard />
    </QueryClientProvider>
  );
}

it('lets a user select a recorded night without mistaking missing data for zero sleep', async () => {
  vi.mocked(api.lifestyle.sleep).mockResolvedValue({
    days: [recorded, missing],
  });
  mount();
  expect(
    await screen.findByText(/No sleep reading. Enable sleep sharing/)
  ).toBeTruthy();
  fireEvent.click(
    screen.getByRole('button', {
      name: /7h 45m asleep, 1 recorded interruptions/,
    })
  );
  expect(
    screen.getByText('7h 45m asleep · 1 recorded interruptions · 15 min awake')
  ).toBeTruthy();
  expect(screen.getByText('97% sleep efficiency')).toBeTruthy();
});

it('does not assign efficiency when sleep coverage is incomplete', async () => {
  vi.mocked(api.lifestyle.sleep).mockResolvedValue({
    days: [{ ...recorded, efficiency: null, unobservedMinutes: 15 }],
  });
  mount();
  expect(
    await screen.findByText(
      'Sleep efficiency unavailable: incomplete recording.'
    )
  ).toBeTruthy();
});

it('explains in-bed-only data', async () => {
  vi.mocked(api.lifestyle.sleep).mockResolvedValue({
    days: [{ ...missing, start: 1, end: 28801 }],
  });
  mount();
  expect(await screen.findByText(/Only time in bed was recorded/)).toBeTruthy();
});

it('reports a failed request', async () => {
  vi.mocked(api.lifestyle.sleep).mockRejectedValue(new Error('Offline'));
  mount();
  expect((await screen.findByRole('alert')).textContent).toContain('Offline');
});
