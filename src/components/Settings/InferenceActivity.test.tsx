// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import type { InferenceActivity as Activity } from '../../lib/inferenceActivity';
import { InferenceActivity } from './InferenceActivity';

function makeActivity(overrides: Partial<Activity> = {}): Activity {
  return {
    events: [],
    counters: { calls: 0, preempted: 0, refused: 0, errors: 0, leaked: 0 },
    lanes: {},
    jobs: [],
    jobCounts: {},
    handlers: ['journal.polish'],
    ...overrides,
  };
}

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <InferenceActivity />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('InferenceActivity', () => {
  it('fetches nothing until it is opened', async () => {
    const spy = vi
      .spyOn(api.settings, 'inferenceActivity')
      .mockResolvedValue(makeActivity());
    renderPanel();

    // Collapsed by default, and the query is gated on that: polling a log
    // nobody opened costs a request every few seconds all day.
    await waitFor(() => expect(spy).not.toHaveBeenCalled());

    fireEvent.click(screen.getByRole('button', { expanded: false }));
    await waitFor(() => expect(spy).toHaveBeenCalled());
  });

  it('shows what ran and what is queued once opened', async () => {
    vi.spyOn(api.settings, 'inferenceActivity').mockResolvedValue(
      makeActivity({
        events: [
          {
            at: 1_757_000_000,
            kind: 'call',
            lane: 'gpu',
            label: 'chat.reply',
            priority: 1,
            ran: 2.5,
            waited: 0,
            outcome: 'ok',
          },
        ],
        counters: { calls: 1, preempted: 0, refused: 0, errors: 0, leaked: 0 },
        jobs: [
          {
            id: 'j1',
            kind: 'journal.polish',
            targetId: 'entry-1',
            status: 'pending',
            attempts: 0,
            cancels: 0,
            error: null,
            createdAt: null,
            startedAt: null,
            finishedAt: null,
          },
        ],
        jobCounts: { pending: 1 },
      })
    );
    renderPanel();
    fireEvent.click(screen.getByRole('button', { expanded: false }));

    expect(await screen.findByText(/chat.reply finished in 2.5s/)).toBeTruthy();
    expect(screen.getByText(/journal.polish for entry-1/)).toBeTruthy();
    expect(screen.getByText(/Since this server started: 1 call/)).toBeTruthy();
  });

  it('calls out a job whose handler no longer exists', async () => {
    // A row like this sits `pending` forever and nothing else says why.
    vi.spyOn(api.settings, 'inferenceActivity').mockResolvedValue(
      makeActivity({
        jobs: [
          {
            id: 'j1',
            kind: 'gone.away',
            targetId: null,
            status: 'pending',
            attempts: 0,
            cancels: 0,
            error: null,
            createdAt: null,
            startedAt: null,
            finishedAt: null,
          },
        ],
        jobCounts: { pending: 1 },
      })
    );
    renderPanel();
    fireEvent.click(screen.getByRole('button', { expanded: false }));

    expect(
      await screen.findByText(/No handler is registered for gone.away/)
    ).toBeTruthy();
  });

  it('summarises the queue in the collapsed header', async () => {
    vi.spyOn(api.settings, 'inferenceActivity').mockResolvedValue(
      makeActivity()
    );
    renderPanel();
    // Nothing fetched yet, so the header carries no counts to mislead with.
    expect(
      screen.getByRole('button', { expanded: false }).textContent
    ).toContain('LLM service activity');
  });
});
