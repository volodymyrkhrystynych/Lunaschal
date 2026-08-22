import { onlineManager } from '@tanstack/react-query';

// In network mode the node reaches its Flask backend over Tailscale, so
// `navigator.onLine` (which only knows "is there a LAN/wifi link") lies: wifi
// can be up while the backend is unreachable. We therefore define "online" as
// *the backend answered /api/health*, polled on an interval and re-checked
// whenever the answer could have changed or could have gone stale: the
// browser's coarse online/offline events, and the tab becoming visible again.

const HEALTH_URL = '/api/health';
// The idle heartbeat, and only the idle one: every real API response resets
// this timer (see `reportFetchOutcome`), so an app in use never pings at all.
// It exists for the case where nothing else is talking to the backend and the
// banner would otherwise go stale — which is why it can afford to be slow.
const POLL_INTERVAL_MS = 60_000;
// Faster while offline. Being wrong in this direction is expensive — queries
// use `networkMode: 'online'`, so every refetch is *paused* until we say
// otherwise — and a phone that dropped one packet should not pay fifteen
// seconds of stale UI for it.
const OFFLINE_POLL_INTERVAL_MS = 3_000;
const PING_TIMEOUT_MS = 5_000;
// A failed ping is confirmed before it counts. One miss on a phone radio (or
// a tab the OS froze mid-request) is normal traffic, not a disconnection, and
// declaring offline on it is what put the yellow banner up for no reason.
const CONFIRM_DELAY_MS = 1_500;

async function pingBackend(): Promise<boolean> {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), PING_TIMEOUT_MS);
    const res = await fetch(HEALTH_URL, {
      credentials: 'include',
      cache: 'no-store',
      signal: controller.signal,
    });
    clearTimeout(timer);
    return res.ok;
  } catch {
    return false;
  }
}

/** Ping now and push the result into react-query's online manager. */
export async function recheckOnline(): Promise<boolean> {
  const ok = await pingBackend();
  onlineManager.setOnline(ok);
  return ok;
}

/**
 * The live listener's controls, while it is running. `null` outside the app
 * (tests importing the module, SSR), where `reportFetchOutcome` becomes a
 * no-op rather than a crash.
 */
let live: { settle: (online: boolean) => void; probe: () => void } | null =
  null;

/**
 * What the last real API request proved about the backend, reported from
 * `fetchWithTimeout` — the one place every read and write passes through.
 *
 * This is the primary signal; the poll above is the fallback for when nothing
 * is happening. Traffic the app was making anyway is both cheaper than a probe
 * and better evidence: it is the very request whose success or failure the user
 * is waiting on, rather than a proxy for it.
 *
 *   - `reachable`   — an HTTP response arrived (any status: a 500 still proves
 *                     the server is there).
 *   - `unreachable` — the request never landed. Offline, immediately: this is
 *                     the failure itself, not a guess about one, and the write
 *                     that just failed is retrying behind it.
 *   - `slow`        — we stopped waiting. An overloaded backend is still
 *                     reachable, so this asks the probe rather than deciding.
 */
export function reportFetchOutcome(
  outcome: 'reachable' | 'unreachable' | 'slow'
): void {
  if (outcome === 'slow') {
    live?.probe();
    return;
  }
  const ok = outcome === 'reachable';
  if (live) {
    live.settle(ok);
    return;
  }
  // No listener running (a test, or the window between module load and
  // install). The verdict is still a fact about the backend, so it stands —
  // it just doesn't reschedule anything.
  if (onlineManager.isOnline() !== ok) {
    logIfChanged(ok);
    onlineManager.setOnline(ok);
  }
}

// Log every transition — whether the app thinks it's offline is the single most
// important fact when debugging why cached data isn't showing, and it's
// invisible otherwise.
let lastLogged: boolean | null = null;
function logIfChanged(online: boolean) {
  if (online !== lastLogged) {
    lastLogged = online;
    console.info(`[offline] backend ${online ? 'reachable' : 'UNREACHABLE'}`);
  }
}

/**
 * Replace react-query's default navigator-based online detection with a
 * backend-reachability poll. Call once at startup. The listener only runs
 * while onlineManager has subscribers (react-query keeps it subscribed for the
 * lifetime of the app), and its cleanup clears the timer + event handlers.
 *
 * It reschedules itself after each check rather than running on a fixed
 * interval, because the right gap depends on the answer: a quiet heartbeat
 * while things work, a much tighter one while they don't.
 */
export function installBackendOnlineManager(): void {
  // Seed synchronously so a cold boot while offline is known-offline before the
  // first query runs. `navigator.onLine === false` is trustworthy (no link);
  // `true` only means "link up", so the ping below re-verifies the backend.
  if (typeof navigator !== 'undefined' && navigator.onLine === false) {
    onlineManager.setOnline(false);
  }

  // Reachable from the browser console for manual testing:
  //   await window.__lunaschalRecheckOnline()
  (window as unknown as Record<string, unknown>).__lunaschalRecheckOnline =
    recheckOnline;

  onlineManager.setEventListener(setOnline => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let inFlight: Promise<void> | null = null;

    const hidden = () =>
      typeof document !== 'undefined' && document.visibilityState === 'hidden';

    const schedule = (delay: number) => {
      clearTimeout(timer);
      timer = setTimeout(() => void update(), delay);
    };

    /** Record a verdict and set the next check. Every path that learns
     * something — a probe, or a real request via `reportFetchOutcome` — lands
     * here, so the next check is always measured from the last time we
     * actually knew something. */
    const settle = (ok: boolean) => {
      if (cancelled) return;
      // Only announce a *change*: every successful API response lands here, and
      // re-notifying react-query on each one would have it resume paused
      // mutations over and over. The reschedule below is the part that runs
      // every time — which is what keeps the idle heartbeat from firing while
      // the app is busy proving the same point with real traffic.
      if (onlineManager.isOnline() !== ok) {
        logIfChanged(ok);
        setOnline(ok);
      }
      schedule(ok ? POLL_INTERVAL_MS : OFFLINE_POLL_INTERVAL_MS);
    };

    const update = async (): Promise<void> => {
      if (cancelled) return;
      // A ping from a backgrounded tab measures the browser's throttling, not
      // the backend: phones freeze the tab, the request never leaves, and the
      // answer to a question nobody asked is "offline". The visibility handler
      // below checks the moment it matters instead.
      if (hidden()) {
        schedule(POLL_INTERVAL_MS);
        return;
      }
      if (inFlight) return inFlight;
      inFlight = (async () => {
        let ok = await pingBackend();
        // Going offline is the expensive direction, so it takes two misses.
        // Coming back is instant — one good answer is proof.
        if (!ok && onlineManager.isOnline() && !cancelled) {
          await new Promise(resolve => setTimeout(resolve, CONFIRM_DELAY_MS));
          if (cancelled) return;
          ok = await pingBackend();
        }
        if (cancelled) return;
        settle(ok);
      })();
      try {
        await inFlight;
      } finally {
        inFlight = null;
      }
    };

    // Browser events are cheap triggers: a reported disconnect is trustworthy
    // (go offline immediately); a reported connect only means "link up", so
    // re-verify against the backend before declaring online.
    const onOnline = () => void update();
    const onOffline = () => {
      logIfChanged(false);
      setOnline(false);
    };
    // Coming back to the tab is the moment the answer matters and the moment
    // it is most likely stale — a phone that slept through a poll would
    // otherwise show the offline banner, with every refetch paused behind it,
    // until the next tick came round.
    const onVisible = () => {
      if (!hidden()) void update();
    };
    window.addEventListener('online', onOnline);
    window.addEventListener('offline', onOffline);
    document.addEventListener('visibilitychange', onVisible);
    // bfcache restores (iOS back-swipe) fire pageshow, not visibilitychange.
    window.addEventListener('pageshow', onVisible);

    live = { settle, probe: () => void update() };
    void update();

    return () => {
      cancelled = true;
      live = null;
      clearTimeout(timer);
      window.removeEventListener('online', onOnline);
      window.removeEventListener('offline', onOffline);
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('pageshow', onVisible);
    };
  });
}
