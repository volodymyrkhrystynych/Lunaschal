// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { onlineManager } from '@tanstack/react-query';
import {
  recheckOnline,
  installBackendOnlineManager,
  reportFetchOutcome,
} from './onlineManager';

afterEach(() => {
  onlineManager.setOnline(true); // don't leak offline state to other tests
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('recheckOnline', () => {
  it('reports online when /api/health responds ok', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true }) as Response)
    );
    expect(await recheckOnline()).toBe(true);
    expect(onlineManager.isOnline()).toBe(true);
  });

  it('reports offline when the backend is unreachable', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('network down');
      })
    );
    expect(await recheckOnline()).toBe(false);
    expect(onlineManager.isOnline()).toBe(false);
  });

  it('reports offline on a non-ok health response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: false }) as Response)
    );
    expect(await recheckOnline()).toBe(false);
  });
});

describe('the health poll', () => {
  /** Start the listener the way the app does: install it, then subscribe (it
   * only runs while onlineManager has subscribers). Returns the unsubscribe,
   * which is also what tears the listener down. */
  const start = () => onlineManager.subscribe(() => {});

  /** A ping queue: each entry is what that call resolves to. */
  const pings = (...results: boolean[]) => {
    const fetchMock = vi.fn(async () => {
      const ok = results.shift() ?? true;
      if (!ok) throw new Error('network down');
      return { ok: true } as Response;
    });
    vi.stubGlobal('fetch', fetchMock);
    return fetchMock;
  };

  it('does not go offline on a single missed ping', async () => {
    // The banner this prevents: a phone drops one packet, every query pauses
    // behind `networkMode: 'online'`, and the reply you were reading vanishes
    // until the next tick.
    vi.useFakeTimers();
    const fetchMock = pings(false, true);
    installBackendOnlineManager();
    const stop = start();

    await vi.advanceTimersByTimeAsync(0);
    expect(onlineManager.isOnline()).toBe(true);

    // The confirming ping answers, and it was a false alarm.
    await vi.advanceTimersByTimeAsync(1500);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(onlineManager.isOnline()).toBe(true);
    stop();
  });

  it('goes offline once a second ping agrees', async () => {
    vi.useFakeTimers();
    pings(false, false);
    installBackendOnlineManager();
    const stop = start();

    await vi.advanceTimersByTimeAsync(1500);
    expect(onlineManager.isOnline()).toBe(false);
    stop();
  });

  it('comes back on one good answer, and checks more often while it is down', async () => {
    vi.useFakeTimers();
    const fetchMock = pings(false, false, true);
    installBackendOnlineManager();
    const stop = start();

    await vi.advanceTimersByTimeAsync(1500);
    expect(onlineManager.isOnline()).toBe(false);

    // 3s, not the 15s heartbeat: recovering fast is the whole point.
    await vi.advanceTimersByTimeAsync(3000);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(onlineManager.isOnline()).toBe(true);
    stop();
  });

  it('checks the moment the tab comes back rather than waiting for the tick', async () => {
    vi.useFakeTimers();
    const fetchMock = pings(true);
    installBackendOnlineManager();
    const stop = start();
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    document.dispatchEvent(new Event('visibilitychange'));
    await vi.advanceTimersByTimeAsync(0);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    stop();
  });

  it('does not ping from a hidden tab', async () => {
    // A frozen tab's request never leaves the phone, so its failure says
    // nothing about the backend — and saying "offline" on it is exactly the
    // false alarm this whole path is about.
    vi.useFakeTimers();
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    const fetchMock = pings(true);
    installBackendOnlineManager();
    const stop = start();

    await vi.advanceTimersByTimeAsync(15_000);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(onlineManager.isOnline()).toBe(true);
    stop();
  });
});

describe('what real requests report', () => {
  const start = () => onlineManager.subscribe(() => {});
  const stubFetch = () => {
    const fetchMock = vi.fn(async () => ({ ok: true }) as Response);
    vi.stubGlobal('fetch', fetchMock);
    return fetchMock;
  };

  it('goes offline the moment a request fails to reach the backend', async () => {
    // No confirming ping here, unlike a missed heartbeat: a request that never
    // landed *is* the evidence, and the write behind it is retrying right now.
    vi.useFakeTimers();
    stubFetch();
    installBackendOnlineManager();
    const stop = start();
    await vi.advanceTimersByTimeAsync(0);

    reportFetchOutcome('unreachable');
    expect(onlineManager.isOnline()).toBe(false);
    stop();
  });

  it('a successful request holds the heartbeat off', async () => {
    // Traffic is the signal. An app in use never pings at all — which matters
    // on a phone, where each ping is a fresh TLS connection.
    vi.useFakeTimers();
    const fetchMock = stubFetch();
    installBackendOnlineManager();
    const stop = start();
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(1); // the one at startup

    for (let i = 0; i < 5; i++) {
      await vi.advanceTimersByTimeAsync(30_000);
      reportFetchOutcome('reachable');
    }

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(onlineManager.isOnline()).toBe(true);
    stop();
  });

  it('asks the probe about a request that merely timed out', async () => {
    // An overloaded backend is slow, not gone — and calling it gone would pause
    // every query against a server that is answering, just not quickly.
    vi.useFakeTimers();
    const fetchMock = stubFetch();
    installBackendOnlineManager();
    const stop = start();
    await vi.advanceTimersByTimeAsync(0);

    reportFetchOutcome('slow');
    await vi.advanceTimersByTimeAsync(0);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(onlineManager.isOnline()).toBe(true);
    stop();
  });
});
