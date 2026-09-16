// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api } from '@/hooks/api';
import { CollectionImport } from './CollectionImport';

vi.mock('@/hooks/api', () => ({
  api: {
    fanfic: {
      collections: {
        list: vi.fn(),
        start: vi.fn(),
      },
    },
  },
}));

beforeEach(() => {
  vi.mocked(api.fanfic.collections.list).mockResolvedValue([]);
  vi.mocked(api.fanfic.collections.start)
    .mockReset()
    .mockResolvedValue({ id: 'scan' });
});

function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <CollectionImport />
    </QueryClientProvider>
  );
}

it('starts favorites and follows together by default', async () => {
  setup();
  fireEvent.click(
    screen.getByRole('button', { name: 'Start / resume import' })
  );
  await waitFor(() =>
    expect(api.fanfic.collections.start).toHaveBeenCalledWith(
      'fanfiction.net',
      'all',
      ''
    )
  );
});

it('requires an AO3 username and offers work subscriptions', async () => {
  setup();
  fireEvent.change(screen.getByLabelText('Collection site'), {
    target: { value: 'archiveofourown.org' },
  });
  expect(
    screen.getByRole('button', { name: 'Start / resume import' })
  ).toHaveProperty('disabled', true);
  fireEvent.change(screen.getByLabelText('AO3 username'), {
    target: { value: ' Reader ' },
  });
  fireEvent.change(screen.getByLabelText('Collection'), {
    target: { value: 'subscriptions' },
  });
  fireEvent.click(
    screen.getByRole('button', { name: 'Start / resume import' })
  );
  await waitFor(() =>
    expect(api.fanfic.collections.start).toHaveBeenCalledWith(
      'archiveofourown.org',
      'subscriptions',
      'Reader'
    )
  );
});

it('switches Patreon to the accessible feed and explains the media limit', async () => {
  setup();
  fireEvent.change(screen.getByLabelText('Collection site'), {
    target: { value: 'patreon.com' },
  });
  fireEvent.click(
    screen.getByRole('button', { name: 'Start / resume import' })
  );
  await waitFor(() =>
    expect(api.fanfic.collections.start).toHaveBeenCalledWith(
      'patreon.com',
      'feed',
      ''
    )
  );
  expect(
    screen.getByText(/attachment downloads are not included/)
  ).toBeTruthy();
});

it('reports a failed scan as stopped and preserves its counts', async () => {
  vi.mocked(api.fanfic.collections.list).mockResolvedValue([
    {
      id: 'scan',
      site: 'fanfiction.net',
      collection: 'all',
      username: '',
      status: 'error',
      found: 8,
      imported: 6,
      skipped: 0,
      pages: 2,
      error: 'Session expired',
      retryAfter: 0,
    },
  ]);
  setup();
  expect(await screen.findByText('Session expired')).toBeTruthy();
  expect(screen.getByRole('status').textContent).toContain(
    'Stopped · 2 pages · 6 queued · 2 already in library'
  );
});

it('shows a deferred scan as retrying rather than scanning or stopped', async () => {
  // The scan is still pending and still holds its page position; the error
  // is what it is waiting out, not something the user has to act on.
  vi.mocked(api.fanfic.collections.list).mockResolvedValue([
    {
      id: 'scan',
      site: 'archiveofourown.org',
      collection: 'bookmarks',
      username: 'reader',
      status: 'pending',
      found: 20,
      imported: 20,
      skipped: 0,
      pages: 1,
      error: '525 Server Error',
      retryAfter: Math.floor(Date.now() / 1000) + 600,
    },
  ]);
  setup();
  const row = await screen.findByRole('status');
  expect(row.textContent).toContain('Retrying at');
  expect(row.textContent).not.toContain('Stopped');
  expect(screen.getByText('525 Server Error').className).toContain('amber');
});
