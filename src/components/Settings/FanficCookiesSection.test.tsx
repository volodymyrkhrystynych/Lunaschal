// @vitest-environment jsdom
/**
 * A rejected cookie save (e.g. the backend's 400 for a truncated
 * copy-paste) used to disappear silently — the mutation failed but nothing
 * rendered, so clicking Save looked like it did nothing. What matters here
 * is that a failed save is visible, and that it doesn't linger once the
 * user starts fixing the input.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  render,
  fireEvent,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { FanficCookiesSection } from './FanficCookiesSection';

vi.mock('../../hooks/api', () => ({
  api: {
    fanfic: {
      cookies: {
        list: vi.fn(),
        put: vi.fn(),
      },
      scanWatched: vi.fn(),
    },
  },
}));

const renderSection = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <FanficCookiesSection />
    </QueryClientProvider>
  );
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.fanfic.cookies.list).mockResolvedValue([
    {
      domain: 'forums.spacebattles.com',
      hasCookie: false,
      updatedAt: null,
      hasUserAgent: false,
    },
    {
      domain: 'forum.questionablequesting.com',
      hasCookie: true,
      updatedAt: '2026-01-01T00:00:00.000Z',
      hasUserAgent: false,
    },
    {
      domain: 'forums.sufficientvelocity.com',
      hasCookie: false,
      updatedAt: null,
      hasUserAgent: false,
    },
  ]);
});

it('shows the backend error when a save is rejected, instead of doing nothing', async () => {
  vi.mocked(api.fanfic.cookies.put).mockRejectedValue(
    new Error(
      "Cookie contains a '…' truncation artifact — the copy method cut off a long value."
    )
  );
  renderSection();

  const row = (await screen.findByText('forums.spacebattles.com')).closest(
    'div'
  )!.parentElement as HTMLElement;
  const scoped = within(row);
  fireEvent.change(scoped.getByRole('textbox'), {
    target: { value: 'xf_user=u123; cf_clearance=abc…def' },
  });
  fireEvent.click(scoped.getByRole('button', { name: 'Save' }));

  expect(await scoped.findByText(/truncation artifact/)).toBeTruthy();
});

it('clears a stale save error once the user edits the input again', async () => {
  vi.mocked(api.fanfic.cookies.put).mockRejectedValue(new Error('rejected'));
  renderSection();

  const row = (await screen.findByText('forums.spacebattles.com')).closest(
    'div'
  )!.parentElement as HTMLElement;
  const scoped = within(row);
  fireEvent.change(scoped.getByRole('textbox'), { target: { value: 'bad' } });
  fireEvent.click(scoped.getByRole('button', { name: 'Save' }));
  await scoped.findByText('rejected');

  fireEvent.change(scoped.getByRole('textbox'), {
    target: { value: 'bad-again' },
  });
  await waitFor(() => expect(scoped.queryByText('rejected')).toBeNull());
});

it('flags a stored cookie with no captured User-Agent', async () => {
  renderSection();
  await screen.findByText('forum.questionablequesting.com');
  expect(screen.queryByText(/default UA/)).toBeTruthy();
});
