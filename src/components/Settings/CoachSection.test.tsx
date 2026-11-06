// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, type CoachSettings } from '@/hooks/api';
import { CoachSection } from './CoachSection';

vi.mock('@/hooks/api', () => ({
  api: {
    coach: {
      settings: vi.fn(),
      updateSettings: vi.fn(),
      stats: vi.fn(),
      pushStatus: vi.fn(),
      pushTest: vi.fn(),
    },
  },
}));

const settings: CoachSettings = {
  enabled: false,
  checkinEnabled: false,
  checkinHour: 21,
  quietStart: 22,
  quietEnd: 8,
  dailyCap: 3,
  noSendRate: 0.25,
  behaviors: { weight: true, calories: true, habits: true },
};

function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <CoachSection />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.coach.settings).mockResolvedValue(settings);
  vi.mocked(api.coach.updateSettings).mockImplementation(async c => ({
    ...settings,
    ...c,
  }));
  vi.mocked(api.coach.pushStatus).mockResolvedValue({
    configured: false,
    devices: [],
  });
  vi.mocked(api.coach.stats).mockResolvedValue([
    {
      behavior: 'weight',
      label: 'Weigh-in',
      sent: 12,
      held: 4,
      skipped: 2,
      doneWhenSent: 0.75,
      doneWhenHeld: 0.25,
      arms: { identity: { mean: 0.8, n: 6 } },
      slots: {},
    },
  ]);
});

it('switches the coach on and saves a behaviour change', async () => {
  mount();
  fireEvent.click(await screen.findByLabelText('Reminders that learn'));
  await waitFor(() =>
    expect(api.coach.updateSettings).toHaveBeenCalledWith({ enabled: true })
  );
  fireEvent.click(screen.getByLabelText('Calories'));
  await waitFor(() =>
    expect(api.coach.updateSettings).toHaveBeenCalledWith({
      behaviors: { weight: true, calories: false, habits: true },
    })
  );
});

it('shows what reminders changed, with how much evidence', async () => {
  mount();
  expect(await screen.findByRole('cell', { name: 'Weigh-in' })).toBeTruthy();
  expect(screen.getByText('75%')).toBeTruthy();
  expect(screen.getByText('identity (n≈6)')).toBeTruthy();
});

it('says when push is not configured and disables the test', async () => {
  mount();
  expect(await screen.findByText(/not configured on the server/)).toBeTruthy();
  expect(
    (screen.getByRole('button', { name: 'Send test' }) as HTMLButtonElement)
      .disabled
  ).toBe(true);
});
