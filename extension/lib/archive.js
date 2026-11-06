// Journal links printed to PDF by this browser — the one that is logged in.
//
// Lunaschal queues every non-YouTube link pasted into a journal entry
// (backend/journal/web_archive.py); the archive control tab polls for them,
// opens each in a background tab, decides whether it is a login wall, and
// prints it with the DevTools protocol. `chrome.debugger` is what makes that
// possible without a host permission for every site on the web: it reaches a
// tab this extension opened, whatever is loaded in it.

const AUTH_PATH =
  /(^|[/_.-])(log[-_]?in|sign[-_]?in|auth|sso|session|authenticate|oauth2?)([/_.?-]|$)/i;
const AUTH_HOST = /^(accounts?|login|auth|sso|signin|id)\./i;

// Below this much readable text, a page with a password field or paywall
// markers *is* the wall. Above it, the article is there and the login box is
// a sidebar widget, or the subscriber markup is on a page the session can read.
export const SHORT_PAGE_CHARS = 1500;

export function looksLikeAuth(url) {
  try {
    const u = new URL(url);
    return AUTH_HOST.test(u.hostname) || AUTH_PATH.test(u.pathname);
  } catch {
    return false;
  }
}

function sameDocument(a, b) {
  try {
    const x = new URL(a),
      y = new URL(b);
    return x.host === y.host && x.pathname === y.pathname;
  } catch {
    return false;
  }
}

/**
 * Pure: is this page the thing that was shared, or a wall in front of it?
 * `status` is the main document's final HTTP status, `facts` what pageFacts()
 * read out of the rendered page.
 */
export function classify({ requestedUrl, finalUrl, status, facts }) {
  if ([401, 403, 407].includes(status))
    return { verdict: 'login', reason: `The site answered HTTP ${status}.` };
  if (
    finalUrl &&
    !sameDocument(requestedUrl, finalUrl) &&
    looksLikeAuth(finalUrl) &&
    !looksLikeAuth(requestedUrl)
  )
    return { verdict: 'login', reason: 'The link redirected to a sign-in page.' };
  const short = (facts?.textLength ?? 0) < SHORT_PAGE_CHARS;
  if (facts?.visiblePassword && short)
    return { verdict: 'login', reason: 'The page is a sign-in form.' };
  if ((facts?.notFree || facts?.paywallMarker) && short)
    return { verdict: 'login', reason: 'The page is behind a paywall.' };
  return { verdict: 'ok' };
}

// Self-contained: serialized into the page with Runtime.evaluate. It reads the
// rendered DOM and nothing else — no cookies, no page JavaScript state.
export function pageFacts() {
  const visible = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return (
      r.width > 0 &&
      r.height > 0 &&
      s.visibility !== 'hidden' &&
      s.display !== 'none'
    );
  };
  const visiblePassword = Array.from(
    document.querySelectorAll('input[type=password]')
  ).some(visible);

  // schema.org's paywall flag, wherever in the JSON-LD graph it sits.
  let notFree = false;
  const walk = node => {
    if (!node || typeof node !== 'object') return;
    if (Array.isArray(node)) return node.forEach(walk);
    const flag = node.isAccessibleForFree;
    if (flag === false || flag === 'False' || flag === 'false') notFree = true;
    Object.values(node).forEach(walk);
  };
  for (const script of document.querySelectorAll(
    'script[type="application/ld+json"]'
  )) {
    try {
      walk(JSON.parse(script.textContent));
    } catch {
      // A broken JSON-LD block says nothing either way.
    }
  }

  const paywallMarker = !!document.querySelector(
    '[class*="paywall" i], [id*="paywall" i], [data-testid*="paywall" i], .tp-modal, .tp-backdrop'
  );
  const body = document.body;
  const text = (body && (body.innerText ?? body.textContent)) || '';
  return {
    url: location.href,
    title: document.title,
    visiblePassword,
    notFree,
    paywallMarker,
    textLength: text.replace(/\s+/g, ' ').trim().length,
  };
}

// Runs in the page too: walk down so lazy images and infinite-scroll stubs
// load, then back to the top so the PDF starts where a reader would.
export function scrollThrough() {
  return new Promise(resolve => {
    let steps = 0;
    const step = () => {
      window.scrollBy(0, window.innerHeight);
      steps += 1;
      const atEnd =
        window.scrollY + window.innerHeight >=
        document.documentElement.scrollHeight - 2;
      if (atEnd || steps >= 30) {
        window.scrollTo(0, 0);
        setTimeout(resolve, 500);
      } else {
        setTimeout(step, 250);
      }
    };
    step();
  });
}

export const LOAD_TIMEOUT_MS = 45000;
// The background service worker relays the PDF as base64 inside a message,
// and Chrome caps a message at 64 MB; 40 MB of PDF is ~54 MB of base64.
export const MAX_PDF_BYTES = 40 * 1024 * 1024;

const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

function withTimeout(promise, ms, message) {
  let timer;
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(message)), ms);
    }),
  ]).finally(() => clearTimeout(timer));
}

function isWebUrl(url) {
  try {
    const u = new URL(url);
    return (u.protocol === 'http:' || u.protocol === 'https:') && !u.username;
  } catch {
    return false;
  }
}

/**
 * Dependency injection, as in lib/ffn.js: `tabs` and `debuggerApi` are the
 * chrome.* namespaces in the extension and fakes in Vitest.
 */
export function createRunner({
  send,
  tabs,
  debuggerApi,
  state,
  sleep = delay,
  loadTimeout = LOAD_TIMEOUT_MS,
  onJob = () => {},
}) {
  let busy = false;

  async function archive(job) {
    if (!isWebUrl(job.url))
      return { outcome: 'error', message: 'Not a web link.' };
    const tab = await tabs.create({ url: 'about:blank', active: false });
    const target = { tabId: tab.id };
    let attached = false;
    let mainFrame = null;
    let status = null;
    let loaded;
    const loadedPromise = new Promise(resolve => (loaded = resolve));
    const onEvent = (source, method, params) => {
      if (source.tabId !== tab.id) return;
      // The last Document response for the top frame is the status of the page
      // the redirects ended on.
      if (
        method === 'Network.responseReceived' &&
        params.type === 'Document' &&
        (mainFrame == null || params.frameId === mainFrame)
      )
        status = params.response?.status ?? status;
      if (method === 'Page.loadEventFired') loaded();
    };
    const cdp = (method, params = {}) =>
      debuggerApi.sendCommand(target, method, params);
    debuggerApi.onEvent.addListener(onEvent);
    try {
      await debuggerApi.attach(target, '1.3');
      attached = true;
      await cdp('Page.enable');
      await cdp('Network.enable');
      const nav = await cdp('Page.navigate', { url: job.url });
      if (nav?.errorText)
        return { outcome: 'error', message: `Could not load: ${nav.errorText}` };
      mainFrame = nav?.frameId ?? null;
      await withTimeout(
        loadedPromise,
        loadTimeout,
        'The page did not finish loading.'
      );
      // Late layout, consent banners, fonts.
      await sleep(1500);
      await cdp('Runtime.evaluate', {
        expression: `(${scrollThrough.toString()})()`,
        awaitPromise: true,
      });
      const evaluated = await cdp('Runtime.evaluate', {
        expression: `(${pageFacts.toString()})()`,
        returnByValue: true,
      });
      const facts = evaluated?.result?.value || {};
      const verdict = classify({
        requestedUrl: job.url,
        finalUrl: facts.url,
        status,
        facts,
      });
      const base = { title: facts.title || null, finalUrl: facts.url || null };
      if (verdict.verdict === 'login')
        return { ...base, outcome: 'login', message: verdict.reason };

      const printed = await cdp('Page.printToPDF', {
        printBackground: true,
        preferCSSPageSize: true,
      });
      const pdf = printed?.data;
      if (!pdf) return { ...base, outcome: 'error', message: 'Printing produced nothing.' };
      if ((pdf.length * 3) / 4 > MAX_PDF_BYTES)
        return {
          ...base,
          outcome: 'error',
          message: 'The page printed to more than 40 MB.',
        };
      // Best-effort: a background tab may have nothing composited to shoot.
      let poster = null;
      try {
        const shot = await withTimeout(
          cdp('Page.captureScreenshot', { format: 'jpeg', quality: 70 }),
          5000,
          'no poster'
        );
        poster = shot?.data || null;
      } catch {
        poster = null;
      }
      return { ...base, outcome: 'ok', pdf, poster };
    } catch (error) {
      return { outcome: 'error', message: String(error?.message || error) };
    } finally {
      debuggerApi.onEvent.removeListener(onEvent);
      if (attached) await debuggerApi.detach(target).catch(() => {});
      await tabs.remove(tab.id).catch(() => {});
    }
  }

  return {
    async tick() {
      if (busy) return null;
      busy = true;
      try {
        const reply = await send('archivePoll', { clientId: state.clientId });
        const job = reply.job;
        if (!job) return reply;
        state.current = job;
        onJob(job);
        const result = await archive(job);
        await send('archiveResult', {
          clientId: state.clientId,
          attachmentId: job.id,
          result,
        });
        state.current = null;
        state.last = { url: job.url, outcome: result.outcome, message: result.message };
        return reply;
      } finally {
        busy = false;
      }
    },
  };
}
