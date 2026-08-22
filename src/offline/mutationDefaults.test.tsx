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
  useCalorieLog,
  useIdeaCreate,
  useFoodCreate,
  useSelfieUpload,
  usePaperCreate,
  usePaperPageSave,
  usePaperImageAdd,
  usePaperPageAdd,
} from './mutationDefaults';
import { getPageSave, storePageSave } from './pageStore';
import { getPhoto, listPhotos, storePhoto } from './photoStore';
import type {
  CalorieDay,
  DailyTask,
  IdeaSummary,
  JournalEntry,
} from '../hooks/api';

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

  it('journal create: prepends to the first page of an infinite list without duplicating', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, json: async () => ({ id: 'server-id' }) }))
    );
    const qc = makeClient();
    const listKey = ['journal', { curatedTagId: null }];
    const mkEntry = (id: string): JournalEntry => ({
      id,
      content: id,
      rawContent: null,
      title: null,
      tags: null,
      curatedTags: [],
      createdAt: '',
      updatedAt: '',
    });
    qc.setQueryData(listKey, {
      pages: [[mkEntry('p0a'), mkEntry('p0b')], [mkEntry('p1a')]],
      pageParams: [0, 50],
    });

    const { result } = renderHook(() => useJournalCreate(), {
      wrapper: wrapperFor(qc),
    });
    act(() => result.current.mutate({ id: 'abc', content: 'fresh' }));

    await waitFor(() => {
      const data = qc.getQueryData<{ pages: JournalEntry[][] }>(listKey);
      expect(data?.pages[0][0]?.id).toBe('abc'); // newest at top of page 0
      expect(data?.pages[0]).toHaveLength(3);
      expect(data?.pages[1].some(e => e.id === 'abc')).toBe(false); // not duplicated
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

describe('a write that races the link going down', () => {
  it('is queued rather than lost when the request itself fails', async () => {
    // The hole this closes: react-query decides to pause a mutation *before*
    // firing it, so a write issued in the moment the link drops — while the app
    // still believes it is online — fired, failed at the network level, and
    // with no retry went straight to `error`. `onSettled` then invalidated, and
    // the entry the user had just watched appear was rolled back off the
    // screen. Nothing had been saved anywhere.
    const fetchMock = vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    });
    vi.stubGlobal('fetch', fetchMock);

    const qc = makeClient();
    const listKey = ['journal', { curatedTagId: null }];
    qc.setQueryData<JournalEntry[]>(listKey, []);

    const { result } = renderHook(() => useJournalCreate(), {
      wrapper: wrapperFor(qc),
    });

    // Believed online — that is the whole point of this case.
    expect(onlineManager.isOnline()).toBe(true);
    act(() => result.current.mutate({ id: 'abc', content: 'written offline' }));

    // The failing request is what discovers the backend is gone, and the retry
    // behind it lands in the queue instead of on the floor.
    await waitFor(() => expect(onlineManager.isOnline()).toBe(false));
    await waitFor(() =>
      expect(qc.getMutationCache().getAll()[0]?.state.isPaused).toBe(true)
    );

    // …and the entry is still on screen the entire time.
    expect(qc.getQueryData<JournalEntry[]>(listKey)?.[0]?.id).toBe('abc');

    // Coming back replays it, with the id the client minted, so the server's
    // INSERT OR IGNORE makes a repeat a no-op rather than a second entry.
    vi.mocked(fetchMock).mockImplementation(
      async () => ({ ok: true, json: async () => ({ id: 'abc' }) }) as never
    );
    onlineManager.setOnline(true);
    await act(() => qc.resumePausedMutations());
    await waitFor(() =>
      expect(qc.getMutationCache().getAll()[0]?.state.status).toBe('success')
    );
    const body = JSON.parse(
      (fetchMock.mock.calls.at(-1) as unknown as [string, RequestInit])[1]
        .body as string
    );
    expect(body.id).toBe('abc');
  });

  it('does not retry a write the server refused', async () => {
    // A 400 is a decision, not a blip. Retrying it gets the same answer, and
    // burying it behind a retry turns an error the user has to see into a
    // silent one.
    const fetchMock = vi.fn(async () => ({
      ok: false,
      status: 400,
      json: async () => ({ error: 'content required' }),
    }));
    vi.stubGlobal('fetch', fetchMock);

    const qc = makeClient();
    const { result } = renderHook(() => useJournalCreate(), {
      wrapper: wrapperFor(qc),
    });

    act(() => result.current.mutate({ id: 'abc', content: '' }));

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(onlineManager.isOnline()).toBe(true);
  });
});

describe('the capture surfaces that were online-only', () => {
  it("calorie log: counts toward today's total offline, and syncs later", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ id: 'c1' }),
    }));
    vi.stubGlobal('fetch', fetchMock);

    const qc = makeClient();
    qc.setQueryData<CalorieDay>(['lifestyle', 'calories'], {
      date: '2026-08-22',
      entries: [],
      total: 0,
    });

    const { result } = renderHook(() => useCalorieLog(), {
      wrapper: wrapperFor(qc),
    });

    onlineManager.setOnline(false);
    act(() =>
      result.current.mutate({
        id: 'k1',
        date: '2026-08-22',
        description: 'a coke',
        calories: 140,
      })
    );

    await waitFor(() => {
      const day = qc.getQueryData<CalorieDay>(['lifestyle', 'calories']);
      expect(day?.entries.map(e => e.description)).toEqual(['a coke']);
      expect(day?.total).toBe(140);
    });
    expect(fetchMock).not.toHaveBeenCalled();

    onlineManager.setOnline(true);
    await act(() => qc.resumePausedMutations());
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = JSON.parse(
      (fetchMock.mock.calls.at(-1) as unknown as [string, RequestInit])[1]
        .body as string
    );
    expect(body.id).toBe('k1');
  });

  it('idea capture: appears in the list offline under the id it will keep', async () => {
    // The id matters more than it looks: the capture box opens the idea the
    // moment it is written, so the row it opens has to be the row the server
    // will eventually hold — not a placeholder that gets swapped later.
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ id: 'ignored' }),
    }));
    vi.stubGlobal('fetch', fetchMock);

    const qc = makeClient();
    qc.setQueryData<IdeaSummary[]>(['ideas'], []);

    const { result } = renderHook(() => useIdeaCreate(), {
      wrapper: wrapperFor(qc),
    });

    onlineManager.setOnline(false);
    act(() => result.current.mutate({ id: 'i9', rawContent: 'a thought' }));

    await waitFor(() =>
      expect(qc.getQueryData<IdeaSummary[]>(['ideas'])?.[0]?.id).toBe('i9')
    );

    onlineManager.setOnline(true);
    await act(() => qc.resumePausedMutations());
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls.at(-1) as unknown as [
      string,
      RequestInit,
    ];
    // The voice endpoint, so the background polish pass still runs on it.
    expect(url).toBe('/api/ideas/voice');
    expect(JSON.parse(init.body as string).id).toBe('i9');
  });
});

describe('photos, which are the thing that cannot be retyped', () => {
  it('keeps the meal and its picture on the device, and uploads both later', async () => {
    // The whole point of the media queue: a photograph is not recoverable. A
    // meal captured with no signal has to be *saved* — not "saved if the
    // request happens to go through".
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ id: 'm1', media: [] }),
    }));
    vi.stubGlobal('fetch', fetchMock);

    const qc = makeClient();
    const { result } = renderHook(() => useFoodCreate(), {
      wrapper: wrapperFor(qc),
    });

    const photo = new File(['jpegbytes'], 'meal.jpg', { type: 'image/jpeg' });
    await storePhoto('p1', photo, 'food', 'e1');

    onlineManager.setOnline(false);
    act(() =>
      result.current.mutate({ id: 'e1', photoIds: ['p1'], text: 'ramen' })
    );

    await waitFor(() =>
      expect(qc.getMutationCache().getAll()[0]?.state.isPaused).toBe(true)
    );
    expect(fetchMock).not.toHaveBeenCalled();
    // Still on the device — nothing has confirmed it yet.
    expect((await listPhotos()).map(p => p.id)).toEqual(['p1']);

    onlineManager.setOnline(true);
    await act(() => qc.resumePausedMutations());
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());

    const form = (
      fetchMock.mock.calls.at(-1) as unknown as [string, RequestInit]
    )[1].body as FormData;
    expect(form.get('id')).toBe('e1');
    // The ids ride along so the server can tell a replay from a second meal.
    expect(form.get('mediaIds')).toBe(JSON.stringify(['p1']));
    expect(form.get('media')).toBeTruthy();

    // Confirmed stored, and only now let go of.
    await waitFor(async () => expect(await getPhoto('p1')).toBeUndefined());
  });

  it('never lets go of a photo the server refused', async () => {
    // A 4xx is terminal — stop retrying — but "the server would not take it" is
    // not a reason to destroy the only copy of the picture.
    const fetchMock = vi.fn(async () => ({
      ok: false,
      status: 413,
      json: async () => ({ error: 'image is too large' }),
    }));
    vi.stubGlobal('fetch', fetchMock);

    const qc = makeClient();
    const { result } = renderHook(() => useSelfieUpload(), {
      wrapper: wrapperFor(qc),
    });

    await storePhoto(
      'p2',
      new File(['big'], 'selfie.jpg', { type: 'image/jpeg' }),
      'selfie',
      '2026-08-22'
    );
    act(() => result.current.mutate({ photoId: 'p2' }));

    await waitFor(() => expect(result.current.isError).toBe(true));
    const stored = await getPhoto('p2');
    expect(stored?.blob).toBeTruthy();
    expect(stored?.meta.failed).toBe(true);
    expect(stored?.meta.lastError).toMatch(/too large/);
  });
});

describe('paper, which only ever exists on the tablet it was written on', () => {
  it('opens a new paper offline, under ids the server will agree with later', async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ id: 'pap1', pageId: 'page1' }),
    }));
    vi.stubGlobal('fetch', fetchMock);

    const qc = makeClient();
    const { result } = renderHook(() => usePaperCreate(), {
      wrapper: wrapperFor(qc),
    });

    onlineManager.setOnline(false);
    act(() => result.current.mutate({ id: 'pap1', pageId: 'page1' }));

    // The editor reads this: without it, a paper started offline opens onto a
    // paused query with nothing behind it — a blank page for a paper that
    // does exist, right here, on this device.
    await waitFor(() => {
      const detail = qc.getQueryData<{ pages: { id: string }[] }>([
        'paper',
        'pap1',
      ]);
      expect(detail?.pages.map(p => p.id)).toEqual(['page1']);
    });
    expect(fetchMock).not.toHaveBeenCalled();

    onlineManager.setOnline(true);
    await act(() => qc.resumePausedMutations());
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = JSON.parse(
      (fetchMock.mock.calls.at(-1) as unknown as [string, RequestInit])[1]
        .body as string
    );
    expect(body).toEqual({ id: 'pap1', pageId: 'page1' });
  });

  it('uploads the page as it was last written, not as it was first queued', async () => {
    // The reason the mutation carries a page id and nothing else. An afternoon
    // of writing with no signal is one upload, of the afternoon.
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ success: true }),
    }));
    vi.stubGlobal('fetch', fetchMock);

    const qc = makeClient();
    const { result } = renderHook(() => usePaperPageSave(), {
      wrapper: wrapperFor(qc),
    });

    const png = () => new Blob(['snap'], { type: 'image/png' });
    onlineManager.setOnline(false);
    await storePageSave(
      'page1',
      { strokes: '[1]', width: 2100, height: 2970, revision: 1 },
      png()
    );
    act(() => result.current.mutate({ pageId: 'page1' }));
    await waitFor(() =>
      expect(qc.getMutationCache().getAll()[0]?.state.isPaused).toBe(true)
    );

    // …the afternoon continues.
    await storePageSave(
      'page1',
      { strokes: '[1,2,3]', width: 2100, height: 2970, revision: 3 },
      png()
    );

    onlineManager.setOnline(true);
    await act(() => qc.resumePausedMutations());
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    const [url, init] = fetchMock.mock.calls.at(-1) as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe('/api/paper/pages/page1');
    expect(init.method).toBe('PUT');
    const strokes = (init.body as FormData).get('strokes') as Blob;
    expect(await strokes.text()).toBe('[1,2,3]');

    // Confirmed, so the device lets it go.
    await waitFor(async () =>
      expect(await getPageSave('page1')).toBeUndefined()
    );
  });

  it('adds a page offline, and never sends it before the paper exists', async () => {
    // The ordering is the whole point. `resumePausedMutations` replays in
    // parallel, so without a shared scope the page-add can reach the server
    // before the paper it belongs to — which answers 404, and a 404 is not a
    // network failure, so nothing retries it and the page is simply gone.
    // The paper's own create is made slow, so "they were sent in order" cannot
    // pass by accident: without the shared scope both requests leave at once
    // and the page-add lands while the paper is still in flight.
    const seen: string[] = [];
    const fetchMock = vi.fn(async (url: string) => {
      seen.push(`start ${url}`);
      if (url === '/api/paper') await new Promise(r => setTimeout(r, 20));
      seen.push(`end ${url}`);
      return { ok: true, json: async () => ({ id: 'x', position: 1 }) };
    });
    vi.stubGlobal('fetch', fetchMock);

    const qc = makeClient();
    const paper = renderHook(() => usePaperCreate(), {
      wrapper: wrapperFor(qc),
    });
    const page = renderHook(() => usePaperPageAdd(), {
      wrapper: wrapperFor(qc),
    });

    onlineManager.setOnline(false);
    act(() => paper.result.current.mutate({ id: 'pap1', pageId: 'page1' }));
    act(() => page.result.current.mutate({ paperId: 'pap1', pageId: 'page2' }));

    // Both pages are on the tablet straight away.
    await waitFor(() => {
      const detail = qc.getQueryData<{ pages: { id: string }[] }>([
        'paper',
        'pap1',
      ]);
      expect(detail?.pages.map(p => p.id)).toEqual(['page1', 'page2']);
    });
    expect(fetchMock).not.toHaveBeenCalled();

    onlineManager.setOnline(true);
    await act(() => qc.resumePausedMutations());
    await waitFor(() => expect(seen).toHaveLength(4));
    expect(seen).toEqual([
      'start /api/paper',
      'end /api/paper',
      'start /api/paper/pap1/pages',
      'end /api/paper/pap1/pages',
    ]);
  });

  it('pastes a picture onto the page with no backend in reach', async () => {
    // The one thing on a page that cannot be redrawn. It has to be on the page
    // immediately, stored on the device, and uploaded under the same id later.
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ id: 'img1' }),
    }));
    vi.stubGlobal('fetch', fetchMock);

    const qc = makeClient();
    qc.setQueryData(['paper', 'page', 'page1'], {
      strokes: '[]',
      width: 2100,
      height: 2970,
      images: [],
    });

    const { result } = renderHook(() => usePaperImageAdd(), {
      wrapper: wrapperFor(qc),
    });

    const box = { x: 10, y: 20, width: 300, height: 400 };
    await storePhoto(
      'img1',
      new File(['pixels'], 'pasted.png', { type: 'image/png' }),
      'paper',
      'page1',
      box
    );

    onlineManager.setOnline(false);
    act(() =>
      result.current.mutate({
        imageId: 'img1',
        pageId: 'page1',
        box,
        filename: 'pasted.png',
      })
    );

    // On the page at once — with no url, because there is no server copy yet.
    // The editor draws it from the device store instead.
    await waitFor(() => {
      const page = qc.getQueryData<{
        images: { id: string; url: string; x: number }[];
      }>(['paper', 'page', 'page1']);
      expect(page?.images).toHaveLength(1);
      expect(page?.images[0]).toMatchObject({ id: 'img1', url: '', x: 10 });
    });
    expect(fetchMock).not.toHaveBeenCalled();

    onlineManager.setOnline(true);
    await act(() => qc.resumePausedMutations());
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());

    const [url, init] = fetchMock.mock.calls.at(-1) as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe('/api/paper/pages/page1/images');
    // Under the id the page already shows, so the replay cannot paste twice.
    expect((init.body as FormData).get('id')).toBe('img1');
    await waitFor(async () => expect(await getPhoto('img1')).toBeUndefined());
  });
});
