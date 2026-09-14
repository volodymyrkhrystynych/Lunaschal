// @vitest-environment jsdom
import { expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api } from '@/hooks/api';
import { SiteLimit } from './SiteLimit';

vi.mock('@/hooks/api', () => ({
  api: { fanfic: { collections: { limit: vi.fn(), resume: vi.fn() } } },
}));
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.fanfic.collections.resume).mockResolvedValue({ success: true });
});
function setup(paused: boolean, cooldownUntil: number) {
  vi.mocked(api.fanfic.collections.limit).mockResolvedValue({
    paused,
    cooldownUntil,
    reason: null,
    interval: 15,
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
  expect(screen.queryByRole('button')).toBeNull();
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
it('hides an expired cooldown', async () => {
  setup(false, 1);
  await waitFor(() => expect(api.fanfic.collections.limit).toHaveBeenCalled());
  expect(screen.queryByRole('status')).toBeNull();
});
