import { describe, expect, it } from 'vitest';
import type { AlertRefreshStatus } from '@/hooks/api';
import { runAlertRefresh } from './alertRefresh';

const result = {
  flagged: 1,
  newImports: 0,
  skippedActive: 0,
  skippedFresh: 0,
  alertsSeen: 3,
  errors: {},
};
const base = { result: null, error: null, startedAt: 1, finishedAt: null };
const noSleep = { sleep: async () => {} };

describe('runAlertRefresh', () => {
  it('polls until the refresh is done', async () => {
    const replies: AlertRefreshStatus[] = [
      { ...base, state: 'running' },
      { ...base, state: 'done', result, finishedAt: 2 },
    ];
    let polls = 0;
    const got = await runAlertRefresh(
      async () => ({ ...base, state: 'running', started: true }),
      async () => replies[polls++],
      noSleep
    );
    expect(got).toEqual(result);
    expect(polls).toBe(2);
  });

  it('returns at once when the server finished inline', async () => {
    const got = await runAlertRefresh(
      async () => ({ ...base, state: 'done', result }),
      async () => {
        throw new Error('should not poll');
      },
      noSleep
    );
    expect(got).toEqual(result);
  });

  it('throws the server error', async () => {
    await expect(
      runAlertRefresh(
        async () => ({ ...base, state: 'error', error: 'forum down' }),
        async () => ({ ...base, state: 'idle' }),
        noSleep
      )
    ).rejects.toThrow('forum down');
  });

  it('throws when a restart lost the refresh', async () => {
    await expect(
      runAlertRefresh(
        async () => ({ ...base, state: 'running' }),
        async () => ({ ...base, state: 'idle' }),
        noSleep
      )
    ).rejects.toThrow('lost');
  });
});
