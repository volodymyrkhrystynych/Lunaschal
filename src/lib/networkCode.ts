// Client-side shelf-life for the network-mode "display code" (the 6-digit
// pseudo-2FA shown in Settings on the server). The server code is stable — it
// only changes via the manual Regenerate button — so once a device has proven
// it can read that code, there's no need to re-enter it on every reconnect.
//
// We remember the code locally for a week: within that window the Login screen
// pre-fills it and only asks for the password; after a week the device forgets
// it and prompts for the code again (a periodic "can you still see the server?"
// re-confirmation). Stored in localStorage like the other per-device prefs (see
// lib/fontSize.ts) rather than the server `settings` table — it's device-local
// by design. The 6-digit code sits here in cleartext; for a single-user
// Tailscale/LAN app that's acceptable, since the shelf-life (not secrecy) is the
// point.
const STORAGE_KEY = 'lunaschal:networkCode';

export const NETWORK_CODE_SHELF_LIFE_MS = 7 * 24 * 60 * 60 * 1000; // 1 week

interface CachedCode {
  code: string;
  savedAt: number;
}

function read(): CachedCode | null {
  let raw: string | null;
  try {
    raw = localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
  if (raw === null) return null;
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (
      parsed &&
      typeof parsed === 'object' &&
      typeof (parsed as CachedCode).code === 'string' &&
      typeof (parsed as CachedCode).savedAt === 'number'
    ) {
      return parsed as CachedCode;
    }
  } catch {
    // fall through
  }
  return null;
}

function isFresh(entry: CachedCode, now: number): boolean {
  const age = now - entry.savedAt;
  return age >= 0 && age < NETWORK_CODE_SHELF_LIFE_MS;
}

/** Persist the code with a fresh timestamp, restarting the shelf-life clock. */
export function saveCachedCode(code: string, now: number = Date.now()): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ code, savedAt: now }));
  } catch {
    // localStorage unavailable (private mode / embedded webview) — the code
    // just won't be remembered, which degrades gracefully to always prompting.
  }
}

export function clearCachedCode(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
}

/**
 * The remembered code if it exists and is still within its shelf-life, else
 * null. A stale entry is cleared as a side effect so it isn't reconsidered.
 */
export function loadCachedCode(now: number = Date.now()): string | null {
  const entry = read();
  if (!entry) return null;
  if (!isFresh(entry, now)) {
    clearCachedCode();
    return null;
  }
  return entry.code;
}

/**
 * Whole days remaining before the cached code expires (rounded up so "expires
 * today" reads as 1), or null when there is no fresh cached code.
 */
export function cacheExpiresInDays(now: number = Date.now()): number | null {
  const entry = read();
  if (!entry || !isFresh(entry, now)) return null;
  const remaining = entry.savedAt + NETWORK_CODE_SHELF_LIFE_MS - now;
  return Math.max(1, Math.ceil(remaining / (24 * 60 * 60 * 1000)));
}
