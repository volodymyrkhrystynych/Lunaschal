// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { api } from '../../hooks/api';
import { useReadingSpans } from './useReadingSpans';

const T0 = new Date('2026-07-13T21:00:00').getTime();

let el: HTMLDivElement;
let save: ReturnType<typeof vi.spyOn>;

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

function setup(chapterId: string | null = 'ch1') {
  return renderHook(
    ({ chapter }) => useReadingSpans('fic1', chapter, { current: el }),
    {
      wrapper,
      initialProps: { chapter: chapterId },
    }
  );
}

/** Move the clock, optionally press a key, and scroll the content box. */
function scrollAt(seconds: number, { input = true, top = 100 } = {}) {
  vi.setSystemTime(T0 + seconds * 1000);
  if (input) window.dispatchEvent(new KeyboardEvent('keydown', { key: 's' }));
  el.scrollTop = top;
  el.dispatchEvent(new Event('scroll'));
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(T0);
  el = document.createElement('div');
  Object.defineProperty(el, 'scrollHeight', {
    value: 1100,
    configurable: true,
  });
  Object.defineProperty(el, 'clientHeight', { value: 100, configurable: true });
  document.body.appendChild(el);
  save = vi
    .spyOn(api.fanfic, 'saveReadingSpan')
    .mockResolvedValue({ success: true });
});

afterEach(() => {
  el.remove();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('useReadingSpans', () => {
  it('sends the span when the chapter changes', async () => {
    const { rerender } = setup();
    scrollAt(0, { top: 0 });
    scrollAt(40, { top: 500 });
    expect(save).not.toHaveBeenCalled();
    rerender({ chapter: 'ch2' });
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0]).toMatchObject({
      ficId: 'fic1',
      chapterId: 'ch1',
      startedAt: T0 / 1000,
      endedAt: T0 / 1000 + 40,
      activeSeconds: 40,
      startFraction: 0,
      endFraction: 0.5,
    });
  });

  it('heartbeats a growing span once a minute', async () => {
    setup();
    scrollAt(0);
    scrollAt(30);
    scrollAt(61);
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(save.mock.calls[0][0]).toMatchObject({ activeSeconds: 61 });
  });

  it('ignores scrolls the reader made itself', async () => {
    const { unmount } = setup();
    scrollAt(0);
    // Ten seconds later with no key/touch/wheel: a programmatic scrollTo.
    scrollAt(10, { input: false });
    scrollAt(20, { input: false });
    unmount();
    await Promise.resolve();
    expect(save).not.toHaveBeenCalled();
  });

  it('ignores scrolls while the tab is hidden and flushes on hide', async () => {
    setup();
    scrollAt(0);
    scrollAt(20);
    Object.defineProperty(document, 'hidden', {
      value: true,
      configurable: true,
    });
    document.dispatchEvent(new Event('visibilitychange'));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    scrollAt(30);
    Object.defineProperty(document, 'hidden', {
      value: false,
      configurable: true,
    });
    expect(save.mock.calls[0][0]).toMatchObject({ activeSeconds: 20 });
  });

  it('records nothing without a chapter (PDF fics)', async () => {
    const { unmount } = setup(null);
    scrollAt(0);
    scrollAt(30);
    unmount();
    await Promise.resolve();
    expect(save).not.toHaveBeenCalled();
  });
});
