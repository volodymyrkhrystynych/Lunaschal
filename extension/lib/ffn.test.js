// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from 'vitest';
import {
  allowedPage,
  samePage,
  snapshot,
  createRunner,
  AUTO_RETRY_DELAYS_MS,
  CHALLENGE_GRACE_MS,
  RECHECK_MS,
} from './ffn.js';

const url = 'https://www.fanfiction.net/s/123/1/';

beforeEach(() => {
  document.head.innerHTML = '';
  document.body.innerHTML = '';
});

it('limits navigation to FF.net story and account-list pages', () => {
  expect(allowedPage(url)).toBe(true);
  expect(
    allowedPage('https://www.fanfiction.net/favorites/story.php?p=2')
  ).toBe(true);
  for (const bad of [
    'http://www.fanfiction.net/s/123/1/',
    'https://evil.test/s/123/1/',
    'https://user@www.fanfiction.net/s/123/1/',
    'https://www.fanfiction.net/login.php',
    'javascript:alert(1)',
    'https://www.fanfiction.net:444/s/123/1/',
  ]) {
    expect(allowedPage(bad)).toBe(false);
  }
  expect(samePage(url, `${url}Story-title`)).toBe(true);
  expect(samePage(url, 'https://www.fanfiction.net/s/123/2/')).toBe(false);
  expect(
    samePage(
      'https://www.fanfiction.net/favorites/story.php?p=1',
      'https://www.fanfiction.net/favorites/story.php?p=2'
    )
  ).toBe(false);
});

it('detects rendered challenge and login pages, including HTTP 200 walls', () => {
  document.title = 'Just a moment...';
  expect(snapshot().kind).toBe('challenge');
  document.body.innerHTML = '<form><input type="password"></form>';
  expect(snapshot().kind).toBe('login');
});

it('extracts rendered HTML without scripts and does not mistake story prose for a challenge', () => {
  document.title = 'A story';
  document.body.innerHTML =
    '<div id="storytext">Just a moment, she said.</div><script>secret()</script>';
  const page = snapshot();
  expect(page.kind).toBe('page');
  expect(page.html).toContain('Just a moment, she said.');
  expect(page.html).not.toContain('secret');
});

function setup() {
  const state = { clientId: 'browser-123456789', tabId: null };
  let job = {
    id: 'page1',
    attemptId: 'attempt1',
    url,
    navigate: true,
    needsAttention: false,
  };
  const send = vi.fn(async type =>
    type === 'ffnPoll'
      ? { request: job, browser: { mode: 'browser' } }
      : { success: true }
  );
  const tabs = {
    create: vi.fn(async () => ({ id: 7 })),
    update: vi.fn(async () => ({})),
    get: vi.fn(async () => ({ id: 7, url, status: 'complete' })),
  };
  const scripting = {
    executeScript: vi.fn(async () => [
      { result: { kind: 'page', url, html: '<div id="storytext">text</div>' } },
    ]),
  };
  const storage = {
    set: vi.fn(async () => {}),
    remove: vi.fn(async () => {}),
    get: vi.fn(async () => ({})),
  };
  const save = vi.fn();
  const clock = { t: 1000 };
  const options = {
    state,
    send,
    tabs,
    scripting,
    storage,
    save,
    now: () => clock.t,
  };
  return {
    ...options,
    advance(ms) {
      clock.t += ms;
    },
    runner: createRunner(options),
    setJob(value) {
      job = value ? { attemptId: 'attempt1', ...value } : null;
    },
  };
}

it('navigates once, checkpoints first, then submits the rendered page', async () => {
  const s = setup();
  await s.runner.tick();
  expect(s.save.mock.invocationCallOrder[0]).toBeLessThan(
    s.tabs.update.mock.invocationCallOrder[0]
  );
  expect(s.tabs.update).toHaveBeenCalledWith(7, { url });
  expect(s.scripting.executeScript).not.toHaveBeenCalled();
  s.setJob({ id: 'page1', url, navigate: false });
  await s.runner.tick();
  expect(s.send).toHaveBeenCalledWith(
    'ffnResult',
    expect.objectContaining({
      requestId: 'page1',
      result: expect.objectContaining({ kind: 'page', url }),
    })
  );
  expect(s.tabs.update).toHaveBeenCalledTimes(1);
});

const results = s => s.send.mock.calls.filter(([type]) => type === 'ffnResult');

function challenge(s) {
  s.scripting.executeScript.mockResolvedValue([
    { result: { kind: 'challenge', url } },
  ]);
}

it('holds a challenge tab without reloading it until the user continues', async () => {
  const s = setup();
  await s.runner.tick();
  s.setJob({ id: 'page1', url, navigate: false, needsAttention: true });
  challenge(s);
  for (let i = 0; i < 3; i++) {
    s.advance(AUTO_RETRY_DELAYS_MS.at(-1));
    await s.runner.tick();
  }
  expect(results(s)).toHaveLength(0);
  expect(s.send).not.toHaveBeenCalledWith('ffnRetry', expect.anything());
  await s.runner.tick({ continuePage: true });
  expect(results(s)).toHaveLength(1);
  expect(s.tabs.update).toHaveBeenCalledOnce();
});

it('waits out a passive Cloudflare interstitial instead of blocking on it', async () => {
  const s = setup();
  await s.runner.tick();
  s.setJob({ id: 'page1', url, navigate: false });
  challenge(s);
  await s.runner.tick();
  expect(results(s)).toHaveLength(0);
  s.advance(CHALLENGE_GRACE_MS);
  await s.runner.tick();
  expect(results(s)[0][1].result.kind).toBe('challenge');
});

it('takes a parsed page from a tab that never stops loading, but not a half-parsed one', async () => {
  const s = setup();
  await s.runner.tick();
  s.setJob({ id: 'page1', url, navigate: false });
  s.tabs.get.mockResolvedValue({ id: 7, url, status: 'loading' });
  s.scripting.executeScript.mockResolvedValue([
    {
      result: {
        kind: 'page',
        url,
        html: '<div id="storytext">te',
        ready: false,
      },
    },
  ]);
  await s.runner.tick();
  expect(results(s)).toHaveLength(0);
  s.scripting.executeScript.mockResolvedValue([
    {
      result: {
        kind: 'page',
        url,
        html: '<div id="storytext">text</div>',
        ready: true,
      },
    },
  ]);
  await s.runner.tick();
  expect(results(s)[0][1].result.kind).toBe('page');
});

it('submits a blocked page once the tab turns readable, re-reading at most every RECHECK_MS', async () => {
  const s = setup();
  await s.runner.tick();
  s.setJob({ id: 'page1', url, navigate: false, needsAttention: true });
  challenge(s);
  await s.runner.tick();
  expect(s.scripting.executeScript).toHaveBeenCalledOnce();
  s.scripting.executeScript.mockResolvedValue([
    { result: { kind: 'page', url, html: '<div id="storytext">text</div>' } },
  ]);
  await s.runner.tick();
  expect(s.scripting.executeScript).toHaveBeenCalledOnce();
  s.advance(RECHECK_MS);
  await s.runner.tick();
  expect(results(s)[0][1].result.kind).toBe('page');
  expect(s.tabs.update).toHaveBeenCalledOnce();
});

it('re-navigates an unusable blocked tab after backoff, a bounded number of times', async () => {
  const s = setup();
  await s.runner.tick();
  s.setJob({ id: 'page1', url, navigate: false, needsAttention: true });
  s.scripting.executeScript.mockResolvedValue([
    { result: { kind: 'page', url: 'https://www.fanfiction.net/s/123/2/' } },
  ]);
  const retries = () =>
    s.send.mock.calls.filter(([type]) => type === 'ffnRetry').length;
  await s.runner.tick();
  expect(retries()).toBe(0);
  expect(s.state.autoRetry.nextAt).toBe(s.now() + AUTO_RETRY_DELAYS_MS[0]);
  for (const delay of AUTO_RETRY_DELAYS_MS) {
    s.advance(delay - 1);
    await s.runner.tick();
    const before = retries();
    s.advance(RECHECK_MS);
    await s.runner.tick();
    expect(retries()).toBe(before + 1);
    // The server re-blocks the next attempt; the runner no longer owns it.
    s.advance(RECHECK_MS);
    await s.runner.tick();
  }
  expect(retries()).toBe(AUTO_RETRY_DELAYS_MS.length);
  s.advance(AUTO_RETRY_DELAYS_MS.at(-1) * 10);
  await s.runner.tick();
  s.advance(RECHECK_MS);
  await s.runner.tick();
  expect(retries()).toBe(AUTO_RETRY_DELAYS_MS.length);
  expect(results(s)).toHaveLength(0);
});

it('reopening the controller resumes capture without reloading the page', async () => {
  const s = setup();
  await s.runner.tick();
  s.setJob({ id: 'page1', url, navigate: false });
  const restored = createRunner({
    ...s,
    state: JSON.parse(JSON.stringify(s.state)),
  });
  await restored.tick();
  expect(s.tabs.update).toHaveBeenCalledOnce();
  expect(s.send).toHaveBeenCalledWith(
    'ffnResult',
    expect.objectContaining({ requestId: 'page1' })
  );
});

it('rejects a redirect to another chapter without submitting its HTML', async () => {
  const s = setup();
  await s.runner.tick();
  s.setJob({ id: 'page1', url, navigate: false });
  s.scripting.executeScript.mockResolvedValue([
    {
      result: {
        kind: 'page',
        url: 'https://www.fanfiction.net/s/123/2/',
        html: 'wrong',
      },
    },
  ]);
  await s.runner.tick();
  expect(s.send).toHaveBeenCalledWith(
    'ffnResult',
    expect.objectContaining({
      result: expect.objectContaining({ kind: 'error' }),
    })
  );
});

it('reports a closed tab and retries through the server before opening another', async () => {
  const s = setup();
  await s.runner.tick();
  s.setJob({ id: 'page1', url, navigate: false });
  s.tabs.get.mockRejectedValue(new Error('Tab closed'));
  await s.runner.tick();
  expect(s.send).toHaveBeenCalledWith(
    'ffnResult',
    expect.objectContaining({
      result: expect.objectContaining({ kind: 'error' }),
    })
  );
  await s.runner.retry('page1');
  expect(s.state.tabId).toBeNull();
  expect(s.send).toHaveBeenCalledWith(
    'ffnRetry',
    expect.objectContaining({ requestId: 'page1' })
  );
  expect(s.tabs.create).toHaveBeenCalledTimes(1); // Retry cannot itself navigate.
});

it('honors an observed 429 without sending the rate-limit page as a chapter', async () => {
  const s = setup();
  await s.runner.tick();
  s.setJob({ id: 'page1', url, navigate: false });
  s.storage.get.mockResolvedValue({
    ffnResponse: { tabId: 7, url, status: 429, retryAfter: '7200' },
  });
  await s.runner.tick();
  expect(s.send).toHaveBeenCalledWith(
    'ffnResult',
    expect.objectContaining({
      result: expect.objectContaining({
        kind: 'rate_limit',
        retryAfter: '7200',
      }),
    })
  );
  expect(s.scripting.executeScript).not.toHaveBeenCalled();
});

it('does not navigate when the backend connection fails or no page is due', async () => {
  const s = setup();
  s.setJob(null);
  await s.runner.tick();
  s.send.mockRejectedValue(new Error('Offline'));
  await expect(s.runner.tick()).rejects.toThrow('Offline');
  expect(s.tabs.create).not.toHaveBeenCalled();
  expect(s.tabs.update).not.toHaveBeenCalled();
});

it('a new attempt navigates even when the previous rate-limit acknowledgement was lost', async () => {
  const s = setup();
  await s.runner.tick();
  s.setJob({ id: 'page1', attemptId: 'attempt2', url, navigate: true });
  await s.runner.tick();
  expect(s.tabs.update).toHaveBeenCalledTimes(2);
  expect(s.state.attemptId).toBe('attempt2');
});
