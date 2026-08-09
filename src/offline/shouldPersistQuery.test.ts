import { describe, it, expect } from 'vitest';
import type { Query } from '@tanstack/react-query';
import { shouldPersistQuery } from './shouldPersistQuery';

// Minimal Query-shaped fixture. defaultShouldDehydrateQuery only inspects
// state.status (persist successful queries), so that plus queryKey/data is
// enough to exercise our overrides.
function makeQuery(
  queryKey: unknown[],
  data: unknown,
  status: 'success' | 'pending' | 'error' = 'success'
): Query {
  return {
    queryKey,
    state: { status, data, fetchStatus: 'idle' },
  } as unknown as Query;
}

describe('shouldPersistQuery', () => {
  it('persists a normal successful query', () => {
    expect(shouldPersistQuery(makeQuery(['journal', 'list'], []))).toBe(true);
  });

  it('does not persist a non-successful query', () => {
    expect(
      shouldPersistQuery(makeQuery(['journal', 'list'], undefined, 'pending'))
    ).toBe(false);
  });

  it('never persists the ephemeral active-meeting poll', () => {
    expect(shouldPersistQuery(makeQuery(['meetings', 'active'], {}))).toBe(
      false
    );
  });

  it('never persists the next-drill pick', () => {
    // Restoring one serves a snippet already drilled, and — since the cached
    // value is a whole drill object — one shaped by whichever build wrote it.
    expect(
      shouldPersistQuery(makeQuery(['practice', 'next', '', '', 's1', 0], []))
    ).toBe(false);
  });

  it('still persists the other practice queries', () => {
    expect(shouldPersistQuery(makeQuery(['practice', 'stats'], {}))).toBe(true);
  });

  it('persists an authenticated auth status', () => {
    expect(
      shouldPersistQuery(
        makeQuery(['auth', 'status'], {
          authenticated: true,
          networkMode: true,
        })
      )
    ).toBe(true);
  });

  it('does NOT persist a logged-out auth status', () => {
    expect(
      shouldPersistQuery(
        makeQuery(['auth', 'status'], {
          authenticated: false,
          networkMode: true,
        })
      )
    ).toBe(false);
  });

  it('does not persist an auth status with missing data', () => {
    expect(shouldPersistQuery(makeQuery(['auth', 'status'], undefined))).toBe(
      false
    );
  });
});
