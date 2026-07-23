import type { AuthStatus } from '../hooks/api';

// What the top-level app should render based on the ['auth','status'] query.
//
// The subtlety this exists to get right: `/api/auth/status` always answers with
// HTTP 200 { authenticated: bool } and never 401, so a *successful* response is
// the only authoritative signal. A network error (backend unreachable at
// wake-from-sleep, before Tailscale reconnects) is NOT a logged-out signal — it
// means "don't know yet". Treating that as logged-out is what bounced the user
// to Login on every clamshell open. So:
//
//   - authenticated:true            -> 'app'
//   - authenticated:false (a 200)   -> 'login'   (genuinely logged out)
//   - still fetching, no data yet   -> 'loading'
//   - errored / no known-good data  -> 'reconnecting' (keep the session, retry)
export type AuthGate = 'loading' | 'app' | 'login' | 'reconnecting';

export interface AuthGateInput {
  isLoading: boolean;
  isError: boolean;
  data: AuthStatus | undefined;
}

export function resolveAuthGate({
  isLoading,
  isError,
  data,
}: AuthGateInput): AuthGate {
  if (data) {
    return data.authenticated ? 'app' : 'login';
  }
  // No resolved data. Distinguish "haven't heard back yet" from "the fetch
  // failed" — the latter must not be mistaken for a definitive logged-out.
  if (isError) return 'reconnecting';
  if (isLoading) return 'loading';
  // Settled with neither data nor error is not a real react-query state, but if
  // it ever happens, retry rather than log the user out.
  return 'reconnecting';
}
