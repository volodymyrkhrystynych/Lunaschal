import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, api } from './api';

/** A refusal from the server, shaped the way the real backend answers. */
const refuse = (status: number, error: string) =>
  vi.fn().mockResolvedValue({
    ok: false,
    status,
    json: () => Promise.resolve({ error }),
  });

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('markup saves carry the HTTP status', () => {
  // The newspaper reader has to tell a 409 — another reader moved the markup
  // on, so this revision is stale and re-sending it is pointless — from any
  // other failure. It could not: `saveMarkup` went through the shared `send`,
  // which throws a plain `Error`, so the reader's `instanceof ApiError` branch
  // was unreachable, "Use server copy" never appeared, and the 1.5 s autosave
  // re-sent the same doomed request forever.
  it('rejects a conflicting markup save with the status attached', async () => {
    vi.stubGlobal(
      'fetch',
      refuse(409, 'Markup changed in another reader. Reopen the issue.')
    );
    const failure = await api.newspapers
      .saveMarkup('2026-09-01', { revision: 3, strokes: [] })
      .then(
        () => null,
        (e: unknown) => e
      );
    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).status).toBe(409);
    expect((failure as ApiError).message).toMatch(/another reader/);
  });

  // The narrowness is deliberate, not an oversight. `mutationDefaults`'s
  // `isTerminal` treats an `ApiError` 4xx as permanent and stops retrying it,
  // so promoting every JSON write to `ApiError` would quietly change what the
  // offline queue replays. Only the routes that need the status opt in.
  it('leaves other JSON writes throwing a plain error', async () => {
    vi.stubGlobal('fetch', refuse(409, 'This issue is already archived'));
    const failure = await api.newspapers.setAutoDownload(true).then(
      () => null,
      (e: unknown) => e
    );
    expect(failure).toBeInstanceOf(Error);
    expect(failure).not.toBeInstanceOf(ApiError);
  });
});
