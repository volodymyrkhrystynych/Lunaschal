// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from 'vitest';
import {
  classify,
  createRunner,
  looksLikeAuth,
  pageFacts,
  SHORT_PAGE_CHARS,
} from './archive.js';

const URL_ = 'https://example.com/articles/how-bridges-stand';
const longText = 'An arch carries its load as compression. '.repeat(80);

beforeEach(() => {
  document.head.innerHTML = '';
  document.body.innerHTML = '';
  document.title = '';
});

// jsdom lays nothing out, so "visible" has to be granted per element.
function shown(el) {
  el.getBoundingClientRect = () => ({ width: 120, height: 24 });
  return el;
}

// --- classify ----------------------------------------------------------------

const facts = (over = {}) => ({
  url: URL_,
  title: 'How bridges stand',
  visiblePassword: false,
  notFree: false,
  paywallMarker: false,
  textLength: 5000,
  ...over,
});

it('passes an ordinary article', () => {
  expect(
    classify({ requestedUrl: URL_, finalUrl: URL_, status: 200, facts: facts() })
  ).toEqual({ verdict: 'ok' });
});

it.each([401, 403, 407])('treats HTTP %i as a login wall', status => {
  expect(
    classify({ requestedUrl: URL_, finalUrl: URL_, status, facts: facts() })
      .verdict
  ).toBe('login');
});

it('treats a redirect onto a sign-in page as a login wall', () => {
  for (const finalUrl of [
    'https://example.com/login?next=/articles/how-bridges-stand',
    'https://accounts.example.com/signin',
    'https://example.com/users/sign_in',
  ]) {
    expect(
      classify({ requestedUrl: URL_, finalUrl, status: 200, facts: facts() })
        .verdict
    ).toBe('login');
  }
});

it('does not call a link that *is* a sign-in page a wall for having moved', () => {
  // Sharing a login page on purpose is odd, but it is what was shared.
  const requestedUrl = 'https://example.com/login';
  expect(
    classify({
      requestedUrl,
      finalUrl: 'https://example.com/login/',
      status: 200,
      facts: facts(),
    }).verdict
  ).toBe('ok');
});

it('treats a short page with a visible password field as a sign-in form', () => {
  expect(
    classify({
      requestedUrl: URL_,
      finalUrl: URL_,
      status: 200,
      facts: facts({ visiblePassword: true, textLength: 200 }),
    }).verdict
  ).toBe('login');
});

it('keeps a full article with a sidebar login box', () => {
  expect(
    classify({
      requestedUrl: URL_,
      finalUrl: URL_,
      status: 200,
      facts: facts({ visiblePassword: true, textLength: SHORT_PAGE_CHARS * 3 }),
    }).verdict
  ).toBe('ok');
});

it('treats a short subscriber-only page as a paywall', () => {
  for (const over of [{ notFree: true }, { paywallMarker: true }]) {
    expect(
      classify({
        requestedUrl: URL_,
        finalUrl: URL_,
        status: 200,
        facts: facts({ ...over, textLength: 400 }),
      }).verdict
    ).toBe('login');
  }
});

it('keeps a subscriber article the logged-in session can read in full', () => {
  expect(
    classify({
      requestedUrl: URL_,
      finalUrl: URL_,
      status: 200,
      facts: facts({ notFree: true, paywallMarker: true }),
    }).verdict
  ).toBe('ok');
});

it('recognises auth URLs by host and path, not by any word containing "login"', () => {
  expect(looksLikeAuth('https://example.com/auth/callback')).toBe(true);
  expect(looksLikeAuth('https://sso.example.com/')).toBe(true);
  expect(looksLikeAuth('https://example.com/blog/logins-are-hard-to-design')).toBe(false);
  expect(looksLikeAuth('not a url')).toBe(false);
});

// --- pageFacts ----------------------------------------------------------------

it('reads a visible password field and ignores a hidden one', () => {
  document.body.innerHTML = '<form><input type="password" id="pw"></form>';
  expect(pageFacts().visiblePassword).toBe(false);
  shown(document.getElementById('pw'));
  expect(pageFacts().visiblePassword).toBe(true);
});

it('finds isAccessibleForFree=false anywhere in the JSON-LD graph', () => {
  document.head.innerHTML = `<script type="application/ld+json">
    {"@graph": [{"@type": "WebSite"}, {"@type": "NewsArticle",
      "hasPart": {"isAccessibleForFree": "False"}}]}
  </script><script type="application/ld+json">{broken</script>`;
  expect(pageFacts().notFree).toBe(true);
});

it('spots paywall containers and measures readable text', () => {
  document.title = 'Members only';
  document.body.innerHTML = `<div class="Article-Paywall-Gate">Subscribe</div><p>${longText}</p>`;
  const f = pageFacts();
  expect(f.paywallMarker).toBe(true);
  expect(f.title).toBe('Members only');
  expect(f.textLength).toBeGreaterThan(SHORT_PAGE_CHARS);
});

// --- the runner -----------------------------------------------------------

function fakes({ status = 200, value = facts(), print = 'JVBERi0xLjc=', navError } = {}) {
  const listeners = new Set();
  const sent = [];
  const commands = [];
  const tabs = {
    create: vi.fn(async () => ({ id: 7 })),
    remove: vi.fn(async () => {}),
  };
  const emit = (method, params) =>
    listeners.forEach(l => l({ tabId: 7 }, method, params));
  const debuggerApi = {
    onEvent: {
      addListener: l => listeners.add(l),
      removeListener: l => listeners.delete(l),
    },
    attach: vi.fn(async () => {}),
    detach: vi.fn(async () => {}),
    sendCommand: vi.fn(async (target, method, params) => {
      commands.push(method);
      if (method === 'Page.navigate') {
        if (navError) return { errorText: navError };
        queueMicrotask(() => {
          emit('Network.responseReceived', {
            type: 'Document',
            frameId: 'main',
            response: { status },
          });
          emit('Page.loadEventFired', {});
        });
        return { frameId: 'main' };
      }
      if (method === 'Runtime.evaluate')
        return params.returnByValue ? { result: { value } } : {};
      if (method === 'Page.printToPDF') return { data: print };
      if (method === 'Page.captureScreenshot') return { data: '/9j/4AAQ' };
      return {};
    }),
  };
  const send = vi.fn(async (type, payload) => {
    sent.push([type, payload]);
    if (type === 'archivePoll')
      return { job: { id: 'att1', url: URL_ }, archiver: { queued: 1 } };
    return {};
  });
  return { tabs, debuggerApi, send, sent, commands };
}

function runner(f, extra = {}) {
  return createRunner({
    send: f.send,
    tabs: f.tabs,
    debuggerApi: f.debuggerApi,
    state: { clientId: 'c' },
    sleep: async () => {},
    ...extra,
  });
}

it('prints an ordinary page and posts the PDF with a poster', async () => {
  const f = fakes();
  await runner(f).tick();
  const [, payload] = f.sent.find(([type]) => type === 'archiveResult');
  expect(payload.attachmentId).toBe('att1');
  expect(payload.result).toMatchObject({
    outcome: 'ok',
    pdf: 'JVBERi0xLjc=',
    poster: '/9j/4AAQ',
    title: 'How bridges stand',
    finalUrl: URL_,
  });
  expect(f.debuggerApi.detach).toHaveBeenCalled();
  expect(f.tabs.remove).toHaveBeenCalledWith(7);
});

it('reports a login wall without printing it', async () => {
  const f = fakes({ status: 403 });
  await runner(f).tick();
  const [, payload] = f.sent.find(([type]) => type === 'archiveResult');
  expect(payload.result.outcome).toBe('login');
  expect(payload.result.pdf).toBeUndefined();
  expect(f.commands).not.toContain('Page.printToPDF');
});

it('reports a page that never loads as an error and still cleans up', async () => {
  const f = fakes({ navError: 'net::ERR_NAME_NOT_RESOLVED' });
  await runner(f).tick();
  const [, payload] = f.sent.find(([type]) => type === 'archiveResult');
  expect(payload.result.outcome).toBe('error');
  expect(payload.result.message).toContain('ERR_NAME_NOT_RESOLVED');
  expect(f.tabs.remove).toHaveBeenCalledWith(7);
});

it('refuses to open anything that is not a web link', async () => {
  const f = fakes();
  f.send.mockImplementation(async (type, payload) => {
    f.sent.push([type, payload]);
    return type === 'archivePoll'
      ? { job: { id: 'att1', url: 'file:///etc/passwd' } }
      : {};
  });
  await runner(f).tick();
  expect(f.tabs.create).not.toHaveBeenCalled();
  const [, payload] = f.sent.find(([type]) => type === 'archiveResult');
  expect(payload.result.outcome).toBe('error');
});

it('does nothing when the queue is empty', async () => {
  const f = fakes();
  f.send.mockResolvedValue({ job: null });
  await runner(f).tick();
  expect(f.tabs.create).not.toHaveBeenCalled();
});

it('tells the control tab which page it is printing before it prints', async () => {
  const f = fakes();
  const seen = [];
  await runner(f, { onJob: job => seen.push([job.url, f.commands.length]) }).tick();
  expect(seen).toEqual([[URL_, 0]]);
});
