// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import type { ReactNode } from 'react';
import {
  QueryClient,
  QueryClientProvider,
  onlineManager,
} from '@tanstack/react-query';
import { renderHook, waitFor, act } from '@testing-library/react';
import {
  registerOfflineMutationDefaults,
  useJournalCreate,
  useDailyToggle,
} from './mutationDefaults';
import type { DailyTask, JournalEntry } from '../hooks/api';

function makeClient() {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false, networkMode: 'online' },
      mutations: { networkMode: 'always' },
    },
  });
  registerOfflineMutationDefaults(qc);
  return qc;
}

const wrapperFor = (qc: QueryClient) =>
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };

afterEach(() => {
  onlineManager.setOnline(true);
  vi.restoreAllMocks();
});

describe('offline write queue', () => {
  it('journal create: optimistic insert, pause offline, replay with the client id on reconnect', async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ id: 'server-id' }),
    }));
    vi.stubGlobal('fetch', fetchMock);

    const qc = makeClient();
    const listKey = ['journal', { curatedTagId: null }];
    qc.setQueryData<JournalEntry[]>(listKey, []);

    const { result } = renderHook(() => useJournalCreate(), {
      wrapper: wrapperFor(qc),
    });

    onlineManager.setOnline(false);
    act(() => result.current.mutate({ id: 'abc', content: 'hello offline' }));

    // Optimistically inserted despite being offline…
    await waitFor(() => {
      const list = qc.getQueryData<JournalEntry[]>(listKey);
      expect(list?.[0]?.id).toBe('abc');
      expect(list?.[0]?.content).toBe('hello offline');
    });
    // …and paused, not sent.
    await waitFor(() => expect(result.current.isPaused).toBe(true));
    expect(fetchMock).not.toHaveBeenCalled();

    // Reconnect → the queued write replays with the same client id.
    onlineManager.setOnline(true);
    await act(async () => {
      await qc.resumePausedMutations();
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    const [url, opts] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe('/api/journal');
    expect(opts.method).toBe('POST');
    expect(JSON.parse(opts.body as string)).toMatchObject({
      id: 'abc',
      content: 'hello offline',
    });
  });

  it('daily toggle: optimistically flips done offline', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, json: async () => ({ success: true }) }))
    );
    const qc = makeClient();
    qc.setQueryData<DailyTask[]>(
      ['tasks'],
      [
        {
          id: 't1',
          title: 'Stretch',
          position: 1,
          done: false,
          createdAt: '',
          updatedAt: '',
        },
      ]
    );

    const { result } = renderHook(() => useDailyToggle(), {
      wrapper: wrapperFor(qc),
    });

    onlineManager.setOnline(false);
    act(() => result.current.mutate({ id: 't1', done: false }));

    await waitFor(() => {
      const tasks = qc.getQueryData<DailyTask[]>(['tasks']);
      expect(tasks?.[0]?.done).toBe(true);
    });
  });
});
