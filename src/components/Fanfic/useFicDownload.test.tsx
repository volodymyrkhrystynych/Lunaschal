// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, act, waitFor } from '@testing-library/react';
import { api, type FicChapterSummary, type FicChapter } from '../../hooks/api';
import { useFicDownload } from './useFicDownload';

function chapters(n: number): FicChapterSummary[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `c${i}`,
    ficId: 'f1',
    position: i,
    title: `Chapter ${i}`,
    category: '',
    wordCount: 100,
    postedAt: null,
    isRead: false,
  }));
}

const wrapperFor = (qc: QueryClient) =>
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };

afterEach(() => vi.restoreAllMocks());

describe('useFicDownload', () => {
  it('fetches every uncached chapter and caches its content', async () => {
    const spy = vi
      .spyOn(api.fanfic, 'chapter')
      .mockImplementation(async (id: string) => ({ id }) as FicChapter);
    const qc = new QueryClient();

    const { result } = renderHook(() => useFicDownload(chapters(3)), {
      wrapper: wrapperFor(qc),
    });
    expect(result.current.total).toBe(3);
    expect(result.current.done).toBe(0);

    await act(async () => {
      await result.current.start();
    });

    await waitFor(() => expect(result.current.status).toBe('done'));
    expect(result.current.done).toBe(3);
    expect(spy).toHaveBeenCalledTimes(3);
    // Content is now in the persisted cache for offline reads.
    expect(qc.getQueryData(['fanfic', 'chapter', 'c1'])).toEqual({ id: 'c1' });
  });

  it('skips chapters already cached from normal reading', async () => {
    const spy = vi
      .spyOn(api.fanfic, 'chapter')
      .mockImplementation(async (id: string) => ({ id }) as FicChapter);
    const qc = new QueryClient();
    qc.setQueryData(['fanfic', 'chapter', 'c0'], { id: 'c0' });

    const { result } = renderHook(() => useFicDownload(chapters(3)), {
      wrapper: wrapperFor(qc),
    });
    expect(result.current.done).toBe(1); // the pre-cached one

    await act(async () => {
      await result.current.start();
    });

    expect(spy).toHaveBeenCalledTimes(2); // only the two uncached chapters
    expect(result.current.done).toBe(3);
  });

  it('reports error but keeps the chapters that succeeded', async () => {
    vi.spyOn(api.fanfic, 'chapter').mockImplementation(async (id: string) => {
      if (id === 'c1') throw new Error('network');
      return { id } as FicChapter;
    });
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    const { result } = renderHook(() => useFicDownload(chapters(3)), {
      wrapper: wrapperFor(qc),
    });

    await act(async () => {
      await result.current.start();
    });

    await waitFor(() => expect(result.current.status).toBe('error'));
    expect(result.current.done).toBe(2); // c0 and c2 cached; c1 failed
  });
});
