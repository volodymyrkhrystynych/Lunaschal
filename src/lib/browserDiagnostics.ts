// Per-tab history survives reloads. No URLs, error messages, typed text or
// entity IDs: only lifecycle signals needed to distinguish reloads/remounts.
const KEY = 'lunaschal:browser-diagnostics:v1';
const LIMIT = 80;
type Signal =
  | 'boot'
  | 'pageshow'
  | 'pagehide'
  | 'visibility'
  | 'error'
  | 'unhandledrejection'
  | 'shell-mount'
  | 'shell-unmount'
  | 'auth-gate'
  | 'view';
interface Entry {
  at: string;
  boot: string;
  signal: Signal;
  detail: string;
}
const boot = String(Date.now()) + '-' + Math.random().toString(36).slice(2, 8);

export function readBrowserDiagnostics(): Entry[] {
  try {
    const value = JSON.parse(sessionStorage.getItem(KEY) ?? '[]');
    return Array.isArray(value) ? value.slice(-LIMIT) : [];
  } catch {
    return [];
  }
}

export function recordBrowserSignal(signal: Signal, detail = '') {
  try {
    const entries = readBrowserDiagnostics();
    entries.push({ at: new Date().toISOString(), boot, signal, detail });
    sessionStorage.setItem(KEY, JSON.stringify(entries.slice(-LIMIT)));
  } catch {
    /* Diagnostics must never take down the app. */
  }
}

export function installBrowserDiagnostics() {
  const navigation = performance.getEntriesByType?.('navigation')[0] as
    PerformanceNavigationTiming | undefined;
  recordBrowserSignal(
    'boot',
    `navigation=${navigation?.type ?? 'unknown'} discarded=${String((document as Document & { wasDiscarded?: boolean }).wasDiscarded ?? 'unknown')}`
  );
  const onShow = (e: PageTransitionEvent) =>
    recordBrowserSignal('pageshow', `persisted=${e.persisted}`);
  const onHide = (e: PageTransitionEvent) =>
    recordBrowserSignal('pagehide', `persisted=${e.persisted}`);
  const onVisibility = () =>
    recordBrowserSignal('visibility', document.visibilityState);
  const onError = () => recordBrowserSignal('error');
  const onRejection = () => recordBrowserSignal('unhandledrejection');
  window.addEventListener('pageshow', onShow);
  window.addEventListener('pagehide', onHide);
  document.addEventListener('visibilitychange', onVisibility);
  window.addEventListener('error', onError);
  window.addEventListener('unhandledrejection', onRejection);
  return () => {
    window.removeEventListener('pageshow', onShow);
    window.removeEventListener('pagehide', onHide);
    document.removeEventListener('visibilitychange', onVisibility);
    window.removeEventListener('error', onError);
    window.removeEventListener('unhandledrejection', onRejection);
  };
}
