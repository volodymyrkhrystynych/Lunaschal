/**
 * The service worker: everything that talks to Lunaschal, and nothing that
 * touches a page.
 *
 * **All backend traffic goes through here.** Content scripts have been subject
 * to CORS since Chrome 85 and Flask sends no CORS headers, so a fetch issued
 * from the page would be blocked; a service-worker fetch covered by
 * `host_permissions` is exempt. The content script therefore owns no URLs and
 * no credentials — it asks over `chrome.runtime.sendMessage` and gets data.
 *
 * Auth is usually a non-event: `backend/auth.py`'s `is_localhost` keys off the
 * Host header, so `http://localhost:5000` skips the middleware entirely. The
 * password is only for a backend reached over Tailscale.
 */
import { downloadFilename } from './lib/filename.js';

const DEFAULT_BASE_URL = 'http://localhost:5000';

async function settings() {
  const stored = await chrome.storage.local.get(['baseUrl', 'password']);
  return {
    baseUrl: (stored.baseUrl || DEFAULT_BASE_URL).replace(/\/+$/, ''),
    password: stored.password || '',
  };
}

async function authHeaders() {
  const { password } = await settings();
  return password ? { 'X-Lunaschal-Password': password } : {};
}

/** One JSON call to the backend. Throws with the server's own message. */
async function api(path, { method = 'GET', body } = {}) {
  const { baseUrl } = await settings();
  let response;
  try {
    response = await fetch(`${baseUrl}${path}`, {
      method,
      headers: { 'Content-Type': 'application/json', ...(await authHeaders()) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    // Distinguished from an HTTP error because the fix is different: the
    // server is not running, or the options page points somewhere wrong.
    throw new Error(`Could not reach Lunaschal at ${baseUrl}. Is it running?`);
  }

  const text = await response.text();
  let parsed = null;
  try {
    parsed = text ? JSON.parse(text) : null;
  } catch {
    parsed = null;
  }
  if (!response.ok) {
    throw new Error(parsed?.error || `Lunaschal returned ${response.status}.`);
  }
  return parsed;
}

// --------------------------------------------------------------------------
// Which application belongs to a tab
// --------------------------------------------------------------------------
//
// Remembered per tab in session storage rather than recomputed: the user may
// have overridden the URL match by hand in the popup, and that choice has to
// survive navigating to the next page of a multi-step form.

const tabKey = tabId => `tab:${tabId}`;

async function rememberApplication(tabId, applicationId) {
  await chrome.storage.session.set({ [tabKey(tabId)]: applicationId });
}

async function applicationForTab(tabId, url) {
  const stored = await chrome.storage.session.get(tabKey(tabId));
  const remembered = stored[tabKey(tabId)];
  if (remembered) return remembered;

  if (!url) return null;
  const found = await api(
    `/api/jobs/applications/for-url?url=${encodeURIComponent(url)}`
  );
  const id = found?.application?.id || null;
  if (id) await rememberApplication(tabId, id);
  return id;
}

chrome.tabs.onRemoved.addListener(tabId => {
  chrome.storage.session.remove(tabKey(tabId));
});

// --------------------------------------------------------------------------
// Context menu
// --------------------------------------------------------------------------

const MENU = {
  answer: 'lunaschal-answer',
  fill: 'lunaschal-fill',
  resume: 'lunaschal-resume',
};

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: MENU.answer,
      title: 'Answer this with Lunaschal',
      contexts: ['editable'],
    });
    chrome.contextMenus.create({
      id: MENU.fill,
      title: 'Fill this form from my profile',
      contexts: ['page', 'editable'],
    });
    chrome.contextMenus.create({
      id: MENU.resume,
      title: 'Attach my tailored resume',
      contexts: ['page', 'editable'],
    });
  });
});

/**
 * Make sure the content script is running in `tabId`.
 *
 * Injected on demand rather than declared in the manifest, so the extension
 * needs no host permission for the sites it works on — a context-menu or
 * toolbar click grants `activeTab` for that tab, and that is enough. It is
 * also what lets it work on a Greenhouse board embedded in a company's own
 * domain, which no fixed match-pattern list could cover.
 */
async function ensureContentScript(tabId) {
  try {
    await chrome.tabs.sendMessage(tabId, { type: 'ping' });
    return;
  } catch {
    // Not there yet — fall through and inject.
  }
  await chrome.scripting.insertCSS({
    target: { tabId },
    files: ['content.css'],
  });
  await chrome.scripting.executeScript({
    target: { tabId },
    files: ['content.js'],
  });
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (!tab?.id) return;
  const action = {
    [MENU.answer]: 'answerFocused',
    [MENU.fill]: 'fillForm',
    [MENU.resume]: 'attachResume',
  }[info.menuItemId];
  if (!action) return;

  try {
    await ensureContentScript(tab.id);
    await chrome.tabs.sendMessage(tab.id, { type: action });
  } catch (error) {
    console.error('Lunaschal:', error);
  }
});

// --------------------------------------------------------------------------
// Fetching the resume as bytes
// --------------------------------------------------------------------------

/** Blobs do not survive `chrome.runtime.sendMessage`, so bytes cross as base64. */
function toBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  const CHUNK = 0x8000; // apply() has an argument-count ceiling
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}

const MIME = {
  pdf: 'application/pdf',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
};

async function resumeFile(applicationId, ext = 'pdf') {
  const detail = await api(`/api/jobs/applications/${applicationId}`);
  const version = (detail.resumes || []).find(r => !r.purgedAt);
  if (!version) {
    throw new Error('No resume has been generated for this application yet.');
  }

  const { baseUrl } = await settings();
  const response = await fetch(
    `${baseUrl}/api/jobs/resumes/${version.id}/download.${ext}`,
    { headers: await authHeaders() }
  );
  if (!response.ok) {
    throw new Error(
      ext === 'pdf'
        ? 'That resume has no rendered PDF. Is WeasyPrint installed?'
        : `Could not download the resume (${response.status}).`
    );
  }

  const profile = await api('/api/jobs/profile');
  return {
    name: downloadFilename(profile?.profile?.fullName || '', ext),
    mimeType: MIME[ext],
    base64: toBase64(await response.arrayBuffer()),
  };
}

// --------------------------------------------------------------------------
// Message router
// --------------------------------------------------------------------------

const handlers = {
  async ping() {
    return { ok: true };
  },

  async status({ tabId, url }) {
    const applicationId = await applicationForTab(tabId, url);
    if (!applicationId) return { application: null };
    const detail = await api(`/api/jobs/applications/${applicationId}`);
    return {
      application: {
        id: detail.id,
        title: detail.title,
        company: detail.company,
        status: detail.status,
        hasResume: (detail.resumes || []).some(r => !r.purgedAt),
        recordedCount: (detail.recordedAnswers || []).length,
      },
    };
  },

  /** Applications with a resume waiting — what the popup offers to pick from. */
  async readyApplications() {
    const applications = await api('/api/jobs/applications?status=ready');
    return { applications };
  },

  async chooseApplication({ tabId, applicationId }) {
    await rememberApplication(tabId, applicationId);
    return { ok: true };
  },

  async answerQuestions({ tabId, url, questions }) {
    const applicationId = await applicationForTab(tabId, url);
    if (!applicationId) {
      throw new Error(
        'No application is linked to this tab. Pick one from the Lunaschal popup.'
      );
    }
    const result = await api(
      `/api/jobs/applications/${applicationId}/answers`,
      { method: 'POST', body: { questions } }
    );
    return { applicationId, answers: result.answers || [] };
  },

  async recordAnswers({ tabId, url, answers }) {
    const applicationId = await applicationForTab(tabId, url);
    if (!applicationId) return { written: 0 };
    const result = await api(
      `/api/jobs/applications/${applicationId}/recorded-answers`,
      { method: 'POST', body: { answers } }
    );
    return { written: result.written };
  },

  async recordFillRun({ tabId, windowId, url, pageTitle, fields }) {
    const applicationId = await applicationForTab(tabId, url);
    if (!applicationId) return { recorded: false };
    let screenshotBase64 = '';
    try {
      const dataUrl = await chrome.tabs.captureVisibleTab(windowId, {
        format: 'png',
      });
      screenshotBase64 = dataUrl.split(',', 2)[1] || '';
    } catch {
      // The page-state record is still valuable when capture permission has
      // expired or the tab is no longer visible.
    }
    const result = await api(
      `/api/jobs/applications/${applicationId}/fill-runs`,
      {
        method: 'POST',
        body: { pageUrl: url, pageTitle, fields, screenshotBase64 },
      }
    );
    return { recorded: true, runId: result.id, screenshot: result.screenshot };
  },

  async resume({ tabId, url, ext }) {
    const applicationId = await applicationForTab(tabId, url);
    if (!applicationId) {
      throw new Error(
        'No application is linked to this tab. Pick one from the Lunaschal popup.'
      );
    }
    return resumeFile(applicationId, ext || 'pdf');
  },

  /** Dictation, through the same local Whisper/Parakeet the rest of the app
   * uses — deliberately not the Web Speech API, which would ship audio to
   * Google from an otherwise entirely local product. */
  async transcribe({ base64, mimeType }) {
    const { baseUrl } = await settings();
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);

    const form = new FormData();
    form.append(
      'audio',
      new Blob([bytes], { type: mimeType }),
      'dictation.webm'
    );
    form.append('source', 'extension');

    const response = await fetch(`${baseUrl}/api/transcribe`, {
      method: 'POST',
      headers: await authHeaders(),
      body: form,
    });
    const parsed = await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error(parsed?.error || 'Transcription failed.');
    }
    return { text: parsed?.text || '' };
  },
};

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const handler = handlers[message?.type];
  if (!handler) return false;

  // A content script does not know its own tab id; the popup has to say which
  // tab it is acting for.
  const tabId = message.tabId ?? sender.tab?.id ?? null;
  const url = message.url ?? sender.tab?.url ?? null;
  const windowId = message.windowId ?? sender.tab?.windowId ?? null;

  handler({ ...message, tabId, windowId, url })
    .then(result => sendResponse({ ok: true, ...result }))
    .catch(error =>
      sendResponse({ ok: false, error: String(error.message || error) })
    );

  // Keeps the message channel open for the async reply above.
  return true;
});
