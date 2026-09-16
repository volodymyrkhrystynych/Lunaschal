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
      scanBookmarks: vi.fn(),
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

it('sends a multi-line request-headers paste to the backend with its newlines intact', async () => {
  // A single-line <input> silently strips newlines from pasted text (HTML's
  // value sanitization algorithm) — that glued every header of a "Copy
  // Request Headers" paste into one string the backend couldn't tell
  // Cookie: and User-Agent: apart in, which is why the field is a
  // <textarea>. This pins that down at the component level.
  vi.mocked(api.fanfic.cookies.put).mockResolvedValue({ success: true });
  renderSection();

  const row = (await screen.findByText('forums.spacebattles.com')).closest(
    'div'
  )!.parentElement as HTMLElement;
  const scoped = within(row);
  const field = scoped.getByRole('textbox');
  expect(field.tagName).toBe('TEXTAREA');

  const dump =
    'GET / HTTP/2\nHost: forums.spacebattles.com\n' +
    'User-Agent: Mozilla/5.0 Firefox/153.0\nCookie: cf_clearance=AAA; xf_user=BBB\n';
  fireEvent.change(field, { target: { value: dump } });
  fireEvent.click(scoped.getByRole('button', { name: 'Save' }));

  await waitFor(() =>
    expect(api.fanfic.cookies.put).toHaveBeenCalledWith(
      'forums.spacebattles.com',
      dump.trim()
    )
  );
});

it('syncs bookmark labels for a forum with a cookie, and not without one', async () => {
  vi.mocked(api.fanfic.scanBookmarks).mockResolvedValue({ started: true });
  renderSection();

  const withCookie = (
    await screen.findByText('forum.questionablequesting.com')
  ).closest('div')!.parentElement as HTMLElement;
  const button = within(withCookie).getByRole('button', {
    name: 'Sync bookmark labels',
  });
  fireEvent.click(button);
  await waitFor(() =>
    expect(api.fanfic.scanBookmarks).toHaveBeenCalledWith(
      'forum.questionablequesting.com'
    )
  );

  // Scanning needs a logged-in session, so the button is dead without one.
  const noCookie = (await screen.findByText('forums.spacebattles.com')).closest(
    'div'
  )!.parentElement as HTMLElement;
  expect(
    within(noCookie)
      .getByRole('button', { name: 'Sync bookmark labels' })
      .hasAttribute('disabled')
  ).toBe(true);
});

it('reports a finished bookmark sync, including what it filed', async () => {
  vi.mocked(api.fanfic.cookies.list).mockResolvedValue([
    {
      domain: 'forums.spacebattles.com',
      hasCookie: true,
      updatedAt: null,
      hasUserAgent: true,
      bookmarkScan: {
        page: 1,
        lastPage: 3,
        found: 12,
        imported: 2,
        alreadyInLibrary: 10,
        foldered: 9,
        done: true,
        error: null,
      },
    },
  ]);
  renderSection();
  expect(await screen.findByText(/12 bookmarks seen/)).toBeTruthy();
  expect(screen.getByText(/9 filed into folders/)).toBeTruthy();
});

it('offers no bookmark sync on AO3, whose bookmarks come in on the collection scan', async () => {
  vi.mocked(api.fanfic.cookies.list).mockResolvedValue([
    {
      domain: 'archiveofourown.org',
      hasCookie: true,
      updatedAt: null,
      hasUserAgent: false,
    },
  ]);
  renderSection();
  await screen.findByText('archiveofourown.org');
  expect(
    screen.queryByRole('button', { name: 'Sync bookmark labels' })
  ).toBeNull();
  expect(screen.getByText(/tags become folders on that scan/)).toBeTruthy();
});
