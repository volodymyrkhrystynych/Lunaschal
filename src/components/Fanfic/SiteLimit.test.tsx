// @vitest-environment jsdom
import { expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api } from '@/hooks/api';
import { SiteLimit } from './SiteLimit';

vi.mock('@/hooks/api', () => ({
  api: {
    fanfic: {
      collections: {
        limit: vi.fn(),
        resume: vi.fn(),
        pause: vi.fn(),
        setInterval: vi.fn(),
      },
    },
  },
}));
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.fanfic.collections.resume).mockResolvedValue({ success: true });
  vi.mocked(api.fanfic.collections.pause).mockResolvedValue({ success: true });
});
function setup(paused: boolean, cooldownUntil: number) {
  vi.mocked(api.fanfic.collections.limit).mockResolvedValue({
    paused,
    cooldownUntil,
    nextRequest: cooldownUntil,
    reason: null,
    interval: 600,
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <SiteLimit />
    </QueryClientProvider>
  );
}
it('shows an automatic cooldown without a bypass button', async () => {
  setup(false, Date.now() / 1000 + 3600);
  expect(
    await screen.findByText('FF.net downloads cooling down.')
  ).toBeTruthy();
  expect(
    screen.queryByRole('button', { name: 'Resume FF.net downloads' })
  ).toBeNull();
  expect(
    screen.getByRole('button', { name: 'Pause FF.net downloads' })
  ).toBeTruthy();
});
it('lets the user explicitly resume after a browser challenge', async () => {
  setup(true, 0);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Resume FF.net downloads' })
  );
  await waitFor(() =>
    expect(api.fanfic.collections.resume).toHaveBeenCalledOnce()
  );
});
it('offers pause during normal imports and explains the ten-minute interval', async () => {
  setup(false, 1);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Pause FF.net downloads' })
  );
  await waitFor(() =>
    expect(api.fanfic.collections.pause).toHaveBeenCalledOnce()
  );
  expect(screen.getByText(/one request every 10 minutes/)).toBeTruthy();
});

it('saves an interval in minutes as seconds and shows the saved value', async () => {
  setup(false, 0);
  const input = await screen.findByRole('spinbutton', {
    name: /Minutes between FF.net requests/,
  });
  expect(input).toHaveProperty('value', '10');
  vi.mocked(api.fanfic.collections.setInterval).mockImplementation(async () => {
    vi.mocked(api.fanfic.collections.limit).mockResolvedValue({
      paused: false,
      cooldownUntil: 0,
      nextRequest: 0,
      reason: null,
      interval: 1200,
    });
  });
  fireEvent.change(input, { target: { value: '20' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save interval' }));
  await waitFor(() =>
    expect(
      vi.mocked(api.fanfic.collections.setInterval).mock.calls[0]?.[0]
    ).toBe(1200)
  );
  expect(await screen.findByText(/one request every 20 minutes/)).toBeTruthy();
  expect(input).toHaveProperty('value', '20');
});
