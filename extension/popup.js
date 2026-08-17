/**
 * The toolbar popup: say which application this tab is, trigger the two fill
 * actions, and dictate.
 *
 * Dictation lives here rather than in the content script on purpose.
 * `getUserMedia` called from a content script runs under the *page's*
 * Permissions-Policy and its origin's permission state, so a careers page that
 * does not allow the microphone silently kills it, and every new domain would
 * re-prompt. An extension page has its own stable origin, so permission is
 * granted once.
 */
const $ = id => document.getElementById(id);

function send(type, payload = {}) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage({ type, ...payload }, response => {
      if (chrome.runtime.lastError)
        reject(new Error(chrome.runtime.lastError.message));
      else if (!response?.ok) reject(new Error(response?.error || 'Failed.'));
      else resolve(response);
    });
  });
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

/** The content script is injected on demand, so every action ensures it. */
async function withContentScript(tabId, message) {
  try {
    await chrome.tabs.sendMessage(tabId, { type: 'ping' });
  } catch {
    await chrome.scripting.insertCSS({
      target: { tabId },
      files: ['content.css'],
    });
    await chrome.scripting.executeScript({
      target: { tabId },
      files: ['content.js'],
    });
  }
  return chrome.tabs.sendMessage(tabId, message);
}

let tab = null;

async function refresh() {
  tab = await activeTab();
  if (!tab?.id) return;

  try {
    const { application } = await send('status', {
      tabId: tab.id,
      url: tab.url,
    });
    if (application) {
      $('status').className = 'card';
      $('status').textContent =
        `${application.title} — ${application.company}` +
        (application.hasResume ? '' : ' (no resume generated yet)') +
        (application.recordedCount
          ? ` · ${application.recordedCount} answers saved`
          : '');
      for (const id of ['fill', 'resume', 'dictate']) $(id).disabled = false;
    } else {
      $('status').className = 'card muted';
      $('status').textContent = 'No application linked to this tab yet.';
      await loadPicker();
    }
  } catch (error) {
    $('status').className = 'card error';
    $('status').textContent = error.message;
  }
}

async function loadPicker() {
  try {
    const { applications } = await send('readyApplications');
    if (!applications?.length) return;
    const select = $('applications');
    select.textContent = '';
    for (const application of applications) {
      const option = document.createElement('option');
      option.value = application.id;
      option.textContent = `${application.title} — ${application.company}`;
      select.appendChild(option);
    }
    $('picker').hidden = false;
  } catch {
    // A picker we could not populate is just absent; the status line already
    // carries whatever went wrong.
  }
}

$('choose').addEventListener('click', async () => {
  await send('chooseApplication', {
    tabId: tab.id,
    applicationId: $('applications').value,
  });
  $('picker').hidden = true;
  await refresh();
});

$('fill').addEventListener('click', async () => {
  await withContentScript(tab.id, { type: 'fillForm' });
  window.close();
});

$('resume').addEventListener('click', async () => {
  await withContentScript(tab.id, { type: 'attachResume' });
  window.close();
});

$('options').addEventListener('click', event => {
  event.preventDefault();
  chrome.runtime.openOptionsPage();
});

// --------------------------------------------------------------------------
// Dictation
// --------------------------------------------------------------------------

let recorder = null;
let chunks = [];

function toBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}

async function stopRecording() {
  const button = $('dictate');
  button.textContent = 'Transcribing…';
  button.classList.remove('recording');
  button.disabled = true;

  const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
  for (const track of recorder.stream.getTracks()) track.stop();
  recorder = null;
  chunks = [];

  try {
    const { text } = await send('transcribe', {
      base64: toBase64(await blob.arrayBuffer()),
      mimeType: blob.type,
    });
    if (!text) throw new Error('Nothing was transcribed.');
    const response = await withContentScript(tab.id, {
      type: 'insertDictation',
      text,
    });
    button.textContent = response?.ok
      ? 'Dictate into the last field'
      : 'Click a field on the page first';
  } catch (error) {
    $('status').className = 'card error';
    $('status').textContent = error.message;
    button.textContent = 'Dictate into the last field';
  } finally {
    button.disabled = false;
  }
}

$('dictate').addEventListener('click', async () => {
  if (recorder) {
    recorder.stop();
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    recorder = new MediaRecorder(stream);
    chunks = [];
    recorder.addEventListener(
      'dataavailable',
      e => e.data.size && chunks.push(e.data)
    );
    recorder.addEventListener('stop', stopRecording);
    recorder.start();
    $('dictate').textContent = 'Stop and transcribe';
    $('dictate').classList.add('recording');
  } catch (error) {
    $('status').className = 'card error';
    $('status').textContent = `Microphone unavailable: ${error.message}`;
  }
});

refresh();
