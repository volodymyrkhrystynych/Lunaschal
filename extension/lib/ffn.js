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
  const root = document.documentElement.cloneNode(true);
  root
    .querySelectorAll('script, style, link, iframe')
    .forEach(node => node.remove());
  return { kind: 'page', url, html: root.outerHTML };
}

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
}) {
  let busy = false;
  const persist = () => save(state);
  async function capture(job) {
    const tab = await tabs.get(state.tabId);
    if (tab.status !== 'complete') {
      if (now() - state.startedAt < 120000) return null;
      return { kind: 'error' };
    }
    const { ffnResponse: response } = await storage.get('ffnResponse');
    if (response?.tabId === state.tabId && samePage(job.url, response.url)) {
      if (response.status === 429)
        return { kind: 'rate_limit', retryAfter: response.retryAfter };
      // A completed manual challenge can replace a 403 without a full navigation;
      // inspect the current DOM below rather than relying on stale headers.
    }
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
        if (job.needsAttention && !continuePage) return reply;
        let result;
        try {
          result =
            state.requestId === job.id &&
            state.attemptId === job.attemptId &&
            state.tabId != null
              ? await capture(job)
              : { kind: 'error' };
        } catch {
          result = { kind: 'error' };
        }
        if (result) {
          await send('ffnResult', {
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
        }
        return reply;
      } finally {
        busy = false;
      }
    },
    async retry(requestId) {
      await send('ffnRetry', { clientId: state.clientId, requestId });
      state.requestId = null;
      // A closed tab is recreated on the next permitted attempt.
      try {
        await tabs.get(state.tabId);
      } catch {
        state.tabId = null;
      }
      persist();
    },
  };
}
