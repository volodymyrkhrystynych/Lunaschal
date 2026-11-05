export function allowedPage(url) {
  try {
    const u = new URL(url);
    return (
      u.origin === 'https://www.fanfiction.net' &&
      !u.username &&
      !u.password &&
      /^\/s\/\d+\/\d+\/(?:[^/]*)?$|^\/(favorites|alert)\/story\.php$/.test(
        u.pathname
      )
    );
  } catch {
    return false;
  }
}

export function samePage(expected, actual) {
  if (!allowedPage(expected) || !allowedPage(actual)) return false;
  const a = new URL(expected),
    b = new URL(actual);
  if (a.pathname.startsWith('/s/')) {
    return (
      a.pathname.split('/').slice(2, 4).join('/') ===
      b.pathname.split('/').slice(2, 4).join('/')
    );
  }
  return a.pathname === b.pathname && a.search === b.search;
}

// Self-contained: Chrome copies this function into the isolated content-script
// world. It only reads the rendered DOM, never cookies or page JavaScript.
export function snapshot() {
  const url = location.href;
  if (document.querySelector('input[type=password]'))
    return { kind: 'login', url };
  const readable =
    document.querySelector('#storytext') ||
    document.querySelector('#gui_table1');
  if (
    !readable &&
    (/just a moment|verify.*human|security verification|checking your browser/i.test(
      document.title
    ) ||
      document.querySelector(
        '#challenge-running, #challenge-stage, .cf-turnstile'
      ))
  ) {
    return { kind: 'challenge', url };
  }
  // 'interactive' means the HTML is fully parsed even though FF.net's ads can
  // keep the tab spinning; a page still parsing could be a truncated chapter.
  const ready = document.readyState !== 'loading';
  const root = document.documentElement.cloneNode(true);
  root
    .querySelectorAll('script, style, link, iframe')
    .forEach(node => node.remove());
  return { kind: 'page', url, html: root.outerHTML, ready };
}

// Cloudflare's passive interstitial clears itself within seconds; only a wall
// still standing after this long is reported as needing the user.
export const CHALLENGE_GRACE_MS = 30000;
export const LOAD_TIMEOUT_MS = 120000;
// A blocked page is re-read this often. Reading the open tab costs FF.net nothing.
export const RECHECK_MS = 15000;
// A blocked tab that is not a challenge or sign-in wall is re-navigated after
// these delays, then left for the user. Each retry is still paced by the server.
export const AUTO_RETRY_DELAYS_MS = [60000, 300000, 900000];
// Rejected FF.net HTML gets another chance indefinitely, roughly every ten
// minutes (9–11). The server still owns navigation pacing and cooldowns.
export const PAGE_RETRY_MS = 600000;
export const PAGE_RETRY_JITTER_MS = 60000;

// Dependency injection keeps navigation, retries and challenge handling testable
// without opening FF.net. `save` checkpoints control state before navigation.
export function createRunner({
  send,
  tabs,
  scripting,
  storage,
  save,
  state,
  now = Date.now,
  random = Math.random,
}) {
  let busy = false;
  const persist = () => save(state);
  async function read(job, tab) {
    if (!tab.url?.startsWith('https://www.fanfiction.net/'))
      return { kind: 'error' };
    const [{ result }] = await scripting.executeScript({
      target: { tabId: state.tabId },
      func: snapshot,
    });
    if (result.kind === 'page' && !samePage(job.url, result.url))
      return { kind: 'error' };
    if (
      result.html &&
      new TextEncoder().encode(result.html).length > 4 * 1024 * 1024
    )
      return { kind: 'error' };
    return result;
  }
  // null means "not settled yet, look again on the next tick".
  async function capture(job, { grace = true } = {}) {
    const tab = await tabs.get(state.tabId);
    const elapsed = now() - state.startedAt;
    const { ffnResponse: response } = await storage.get('ffnResponse');
    if (response?.tabId === state.tabId && samePage(job.url, response.url)) {
      if (response.status === 429)
        return { kind: 'rate_limit', retryAfter: response.retryAfter };
      // A completed manual challenge can replace a 403 without a full navigation;
      // inspect the current DOM below rather than relying on stale headers.
    }
    let result;
    try {
      result = await read(job, tab);
    } catch {
      result = { kind: 'error' };
    }
    // A loading subresource must not turn a known wall into a retryable error.
    if (result.kind === 'challenge')
      return grace && elapsed < CHALLENGE_GRACE_MS ? null : result;
    if (result.kind === 'login') return result;
    if (tab.status !== 'complete') {
      // Ads and trackers can keep FF.net "loading" long after the chapter is
      // there; a fully parsed document is good enough.
      if (result.kind === 'page' && result.ready) return result;
      return elapsed < LOAD_TIMEOUT_MS ? null : { kind: 'error' };
    }
    return result;
  }
  async function report(job, result) {
    const reply = await send('ffnResult', {
      clientId: state.clientId,
      requestId: job.id,
      result: { ...result, attemptId: job.attemptId },
    });
    // Preserve the tab/identity for manual verification and for a lost
    // reply acknowledgement. A new request has a new id.
    if (result.kind === 'rate_limit') {
      state.requestId = null;
      persist();
    }
    return reply;
  }
  const owns = job =>
    state.requestId === job.id &&
    state.attemptId === job.attemptId &&
    state.tabId != null;
  async function retry(requestId) {
    await send('ffnRetry', { clientId: state.clientId, requestId });
    state.requestId = null;
    // A closed tab is recreated on the next permitted attempt.
    try {
      await tabs.get(state.tabId);
    } catch {
      state.tabId = null;
    }
    persist();
  }
  // A blocked page is usually fine by the time anyone looks: the challenge
  // cleared, or the tab finished loading. Re-read it, and re-navigate a tab
  // that is unusable for any reason other than a wall only the user can pass.
  async function recover(job) {
    if (now() < (state.recheckAt ?? 0)) return;
    state.recheckAt = now() + RECHECK_MS;
    let result;
    try {
      result = owns(job)
        ? await capture(job, { grace: false })
        : { kind: 'error' };
    } catch {
      result = { kind: 'error' };
    }
    if (!result) return;
    const auto =
      state.autoRetry?.requestId === job.id
        ? state.autoRetry
        : { requestId: job.id, count: 0, nextAt: null };
    state.autoRetry = auto;
    if (result.kind === 'rate_limit') {
      await report(job, result);
      return;
    }
    const pageError = result.kind === 'page';
    if (pageError) {
      // Receipt alone does not mean the parser accepted the HTML. Re-read and
      // submit first, including when the timer expires, so recovery wins.
      const reply = await report(job, result);
      if (reply.accepted !== false) {
        auto.nextAt = null;
        persist();
        return;
      }
    }
    if (result.kind === 'challenge' || result.kind === 'login') {
      // Reloading into a real wall is how a session gets escalated.
      if (auto.nextAt != null) {
        auto.nextAt = null;
        persist();
      }
      return;
    }
    if (auto.pageError !== pageError) {
      auto.pageError = pageError;
      auto.nextAt = null;
    }
    if (!pageError && auto.count >= AUTO_RETRY_DELAYS_MS.length) return;
    if (auto.nextAt == null) {
      const delay = pageError
        ? PAGE_RETRY_MS + (random() * 2 - 1) * PAGE_RETRY_JITTER_MS
        : AUTO_RETRY_DELAYS_MS[auto.count];
      auto.nextAt = now() + delay;
      persist();
      return;
    }
    if (now() < auto.nextAt) return;
    auto.count += 1;
    auto.nextAt = null;
    persist();
    await retry(job.id);
  }
  return {
    async tick({ continuePage = false } = {}) {
      if (busy) return null;
      busy = true;
      try {
        const reply = await send('ffnPoll', { clientId: state.clientId });
        const job = reply.request;
        if (!job) return reply;
        if (!allowedPage(job.url))
          throw new Error('The server returned an unsupported FF.net URL.');
        if (job.navigate) {
          if (state.attemptId !== job.attemptId) {
            if (state.tabId == null) {
              const tab = await tabs.create({
                url: 'about:blank',
                active: false,
              });
              state.tabId = tab.id;
            }
            state.requestId = job.id;
            state.attemptId = job.attemptId;
            state.startedAt = now();
            persist();
            await storage.set({ ffnTabId: state.tabId });
            await storage.remove('ffnResponse');
            await tabs.update(state.tabId, { url: job.url });
          }
          return reply;
        }
        if (job.needsAttention && !continuePage) {
          await recover(job);
          return reply;
        }
        let result;
        try {
          result = owns(job)
            ? await capture(job, { grace: !continuePage })
            : { kind: 'error' };
        } catch {
          result = { kind: 'error' };
        }
        if (result) await report(job, result);
        return reply;
      } finally {
        busy = false;
      }
    },
    retry,
  };
}
