import { describe, it, expect } from 'vitest';
import { resolveAuthGate } from './authGate';
import type { AuthStatus } from '../hooks/api';

const authed: AuthStatus = { authenticated: true, networkMode: true };
const loggedOut: AuthStatus = { authenticated: false, networkMode: true };

describe('resolveAuthGate', () => {
  it('shows the app when a good session is known', () => {
    expect(
      resolveAuthGate({ isLoading: false, isError: false, data: authed })
    ).toBe('app');
  });

  it('shows login on a definitive authenticated:false', () => {
    expect(
      resolveAuthGate({ isLoading: false, isError: false, data: loggedOut })
    ).toBe('login');
  });

  it('shows loading while the first fetch is in flight', () => {
    expect(
      resolveAuthGate({ isLoading: true, isError: false, data: undefined })
    ).toBe('loading');
  });

  it('reconnects (not login) when the fetch errored with no data', () => {
    expect(
      resolveAuthGate({ isLoading: false, isError: true, data: undefined })
    ).toBe('reconnecting');
  });

  it('trusts resolved data even if a later refetch errored', () => {
    // react-query keeps the last good data on a background-refetch error.
    expect(
      resolveAuthGate({ isLoading: false, isError: true, data: authed })
    ).toBe('app');
  });

  it('reconnects on the impossible settled-but-empty state', () => {
    expect(
      resolveAuthGate({ isLoading: false, isError: false, data: undefined })
    ).toBe('reconnecting');
  });
});
