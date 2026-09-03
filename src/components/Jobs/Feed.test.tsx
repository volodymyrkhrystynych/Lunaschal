// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  api,
  type FeedJob,
  type JobApplication,
  type JobPosting,
} from '@/hooks/api';
import { Feed } from './Feed';

afterEach(() => vi.restoreAllMocks());

function feedJob(id: string, title: string): FeedJob {
  return {
    id,
    source: 'greenhouse',
    url: '',
    company: 'Acme',
    title,
    location: 'Toronto',
    remote: false,
    salaryMin: null,
    salaryMax: null,
    salaryCurrency: '',
    description: '',
    matchScore: null,
    dismissed: false,
    postedAt: null,
    createdAt: '2026-08-01T00:00:00Z',
    matchReasons: null,
    triageState: 'pending',
    triageReason: '',
    triageFit: '',
    triageSummary: '',
    triageFlags: [],
    distanceKm: null,
    distancePrecision: '',
    workLocation: '',
    triageAt: null,
    triageError: null,
  };
}

/** A promise the test decides when to settle — this is how a slow POST is
 * held open long enough to prove the card left without waiting for it. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  // The rejection paths below settle these late; without a no-op catch the
  // unhandled rejection surfaces before the component has handled it.
  promise.catch(() => {});
  return { promise, resolve, reject };
}

/** Everything the surrounding panels ask for, answered empty. */
function stubSurroundings() {
  vi.spyOn(api.jobs.searches, 'list').mockResolvedValue([]);
  vi.spyOn(api.jobs.careerWatches, 'list').mockResolvedValue([]);
  vi.spyOn(api.jobs.workdayBoards, 'list').mockResolvedValue([]);
  vi.spyOn(api.jobs, 'pauseState').mockResolvedValue({
    paused: false,
    sources: 0,
    pendingTriage: 0,
  });
  vi.spyOn(api.jobs, 'queueStatus').mockResolvedValue({
    running: false,
    current: null,
    last: null,
    pending: 0,
    failed: 0,
  });
  vi.spyOn(api.jobs, 'triageStatus').mockResolvedValue({
    enabled: true,
    pending: 0,
    rejected: 0,
    failed: 0,
    running: false,
    current: null,
    last: null,
  });
}

function renderFeed() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  render(
    <QueryClientProvider client={client}>
      <Feed />
    </QueryClientProvider>
  );
  return client;
}

describe('Feed decisions', () => {
  beforeEach(stubSurroundings);

  it('drops the card on the tap, not on the response', async () => {
    vi.spyOn(api.jobs, 'feed').mockResolvedValue([
      feedJob('a', 'Engineer A'),
      feedJob('b', 'Engineer B'),
    ]);
    const post = deferred<JobApplication>();
    const queue = vi
      .spyOn(api.jobs, 'queue')
      .mockReturnValue(post.promise as Promise<JobApplication>);

    renderFeed();
    await screen.findByText('Engineer A');

    fireEvent.click(screen.getAllByRole('button', { name: 'Queue' })[0]);

    // The POST is still open: nothing has come back, and the card is gone.
    await waitFor(() => expect(screen.queryByText('Engineer A')).toBeNull());
    expect(queue).toHaveBeenCalledWith('a');
    expect(screen.getByText('Engineer B')).toBeTruthy();
    expect(screen.getByText('Saving 1 decision…')).toBeTruthy();
  });

  it('dismisses the same way', async () => {
    vi.spyOn(api.jobs, 'feed').mockResolvedValue([feedJob('a', 'Engineer A')]);
    const post = deferred<JobPosting>();
    const dismiss = vi
      .spyOn(api.jobs, 'dismiss')
      .mockReturnValue(post.promise as Promise<JobPosting>);

    renderFeed();
    await screen.findByText('Engineer A');

    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));

    await waitFor(() => expect(screen.queryByText('Engineer A')).toBeNull());
    expect(dismiss).toHaveBeenCalledWith('a');
  });

  it('hands the card back, with the reason, when the write fails', async () => {
    vi.spyOn(api.jobs, 'feed').mockResolvedValue([feedJob('a', 'Engineer A')]);
    vi.spyOn(api.jobs, 'queue').mockRejectedValue(new Error('server said no'));

    renderFeed();
    await screen.findByText('Engineer A');
    fireEvent.click(screen.getByRole('button', { name: 'Queue' }));

    expect(
      await screen.findByText("Couldn't queue this — server said no")
    ).toBeTruthy();
    expect(screen.getByText('Engineer A')).toBeTruthy();
  });

  it('keeps a decision hidden when a refetch overtakes its write', async () => {
    // Two taps in a row, and something else — a window refocus, a posting
    // added by hand — refetches the feed while both writes are still open. The
    // server answers with postings it has not been told about yet, and neither
    // may flash back onto the screen.
    vi.spyOn(api.jobs, 'feed').mockResolvedValue([
      feedJob('a', 'Engineer A'),
      feedJob('b', 'Engineer B'),
    ]);
    const post = deferred<JobApplication>();
    vi.spyOn(api.jobs, 'queue').mockReturnValue(
      post.promise as Promise<JobApplication>
    );

    const client = renderFeed();
    await screen.findByText('Engineer A');

    const buttons = screen.getAllByRole('button', { name: 'Queue' });
    fireEvent.click(buttons[0]);
    fireEvent.click(buttons[1]);
    await waitFor(() => expect(screen.queryByText('Engineer B')).toBeNull());

    await client.refetchQueries({ queryKey: ['jobs', 'feed'] });

    expect(screen.queryByText('Engineer A')).toBeNull();
    expect(screen.queryByText('Engineer B')).toBeNull();
    expect(screen.getByText('Saving 2 decisions…')).toBeTruthy();
  });

  it('refetches once for a run of decisions, not once each', async () => {
    // The reconciling fetch belongs to the last write still in flight: a
    // tap-through of a screenful of cards should not cost a fetch per tap.
    const feed = vi
      .spyOn(api.jobs, 'feed')
      .mockResolvedValue([
        feedJob('a', 'Engineer A'),
        feedJob('b', 'Engineer B'),
      ]);
    const first = deferred<JobApplication>();
    const second = deferred<JobApplication>();
    vi.spyOn(api.jobs, 'queue').mockImplementation(jobId =>
      jobId === 'a'
        ? (first.promise as Promise<JobApplication>)
        : (second.promise as Promise<JobApplication>)
    );

    renderFeed();
    await screen.findByText('Engineer A');
    const buttons = screen.getAllByRole('button', { name: 'Queue' });
    fireEvent.click(buttons[0]);
    fireEvent.click(buttons[1]);
    await waitFor(() => expect(screen.queryByText('Engineer B')).toBeNull());
    expect(feed).toHaveBeenCalledTimes(1);

    first.resolve({} as JobApplication);
    // Still one: the second write has not landed, so there is nothing to
    // reconcile against yet.
    await waitFor(() =>
      expect(screen.queryByText('Saving 1 decision…')).toBeTruthy()
    );
    expect(feed).toHaveBeenCalledTimes(1);

    second.resolve({} as JobApplication);
    await waitFor(() => expect(feed).toHaveBeenCalledTimes(2));
  });
});
