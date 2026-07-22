import { onlineManager } from '@tanstack/react-query';

// In network mode the node reaches its Flask backend over Tailscale, so
// `navigator.onLine` (which only knows "is there a LAN/wifi link") lies: wifi
// can be up while the backend is unreachable. We therefore define "online" as
// *the backend answered /api/health*, polled on an interval and re-checked on
// the browser's coarse online/offline events.

const HEALTH_URL = '/api/health';
const POLL_INTERVAL_MS = 15_000;
const PING_TIMEOUT_MS = 5_000;

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
 * Replace react-query's default navigator-based online detection with a
 * backend-reachability poll. Call once at startup. The listener only runs
 * while onlineManager has subscribers (react-query keeps it subscribed for the
 * lifetime of the app), and its cleanup clears the interval + window handlers.
 */
export function installBackendOnlineManager(): void {
  // Seed synchronously so a cold boot while offline is known-offline before the
  // first query runs. `navigator.onLine === false` is trustworthy (no link);
  // `true` only means "link up", so the ping below re-verifies the backend.
  if (typeof navigator !== 'undefined' && navigator.onLine === false) {
    onlineManager.setOnline(false);
  }

  onlineManager.setEventListener(setOnline => {
    let cancelled = false;
    const update = async () => {
      const ok = await pingBackend();
      if (!cancelled) setOnline(ok);
    };

    // Browser events are cheap triggers: a reported disconnect is trustworthy
    // (go offline immediately); a reported connect only means "link up", so
    // re-verify against the backend before declaring online.
    const onOnline = () => void update();
    const onOffline = () => setOnline(false);
    window.addEventListener('online', onOnline);
    window.addEventListener('offline', onOffline);

    void update();
    const interval = setInterval(() => void update(), POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      clearInterval(interval);
      window.removeEventListener('online', onOnline);
      window.removeEventListener('offline', onOffline);
    };
  });
}
