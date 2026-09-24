import { createRunner } from './lib/ffn.js';

const $ = id => document.getElementById(id);
const state = JSON.parse(sessionStorage.getItem('ffn') || 'null') || {
  clientId: crypto.randomUUID(),
  tabId: null,
};
const save = () => sessionStorage.setItem('ffn', JSON.stringify(state));
save();
let connected = false,
  current = null;

async function send(type, payload = {}) {
  const reply = await chrome.runtime.sendMessage({ type, ...payload });
  if (!reply?.ok)
    throw new Error(reply?.error || 'Could not contact Lunaschal.');
  return reply;
}
const runner = createRunner({
  send,
  tabs: chrome.tabs,
  scripting: chrome.scripting,
  storage: chrome.storage.session,
  save,
  state,
});

async function tick(options) {
  if (!connected) return;
  try {
    const reply = await runner.tick(options);
    if (!reply || !connected) return;
    current = reply.request;
    $('error').textContent = '';
    $('status').textContent =
      reply.browser.mode !== 'browser'
        ? 'Browser retrieval is disabled in Lunaschal.'
        : reply.browser.paused
          ? 'Downloads paused in Lunaschal.'
          : current?.needsAttention
            ? current.message
            : current
              ? 'Reading the FF.net tab…'
              : reply.browser.nextRequest * 1000 > Date.now()
                ? `Waiting. Next request no earlier than ${new Date(reply.browser.nextRequest * 1000).toLocaleString()}.`
                : 'Connected. Waiting for queued pages.';
    $('open').disabled = state.tabId == null;
    $('continue').disabled = !current?.needsAttention;
    $('retry').disabled = !current?.needsAttention;
    $('resume').hidden = !reply.browser.paused;
  } catch (error) {
    $('error').textContent = error.message;
  }
}

$('settings').onclick = () => chrome.runtime.openOptionsPage();
$('connect').onclick = async () => {
  try {
    const granted = await chrome.permissions.request({
      origins: ['https://www.fanfiction.net/*'],
    });
    if (!granted)
      throw new Error('FF.net permission is needed to read the download tab.');
    await send('ffnConnect', { clientId: state.clientId });
    connected = true;
    $('connect').disabled = true;
    $('disconnect').disabled = false;
    await tick();
  } catch (error) {
    $('error').textContent = error.message;
  }
};
$('disconnect').onclick = async () => {
  connected = false;
  try {
    await send('ffnDisconnect', { clientId: state.clientId });
  } catch (error) {
    $('error').textContent = error.message;
  }
  $('connect').disabled = false;
  $('disconnect').disabled = true;
  $('continue').disabled = true;
  $('retry').disabled = true;
  $('resume').hidden = true;
  $('status').textContent = 'Disconnected. Saved progress is retained.';
};
$('open').onclick = async () => {
  try {
    await chrome.tabs.update(state.tabId, { active: true });
  } catch {
    $('error').textContent =
      'The download tab was closed. Choose Retry to create another.';
  }
};
$('continue').onclick = () => tick({ continuePage: true });
$('retry').onclick = async () => {
  try {
    await runner.retry(current.id);
    await tick();
  } catch (error) {
    $('error').textContent = error.message;
  }
};
$('resume').onclick = async () => {
  try {
    await send('ffnResume');
    await tick();
  } catch (error) {
    $('error').textContent = error.message;
  }
};
// A regular extension tab owns the timer, so an MV3 service worker suspending
// between requests cannot strand the import. Background-tab throttling may
// lengthen the configured interval, never shorten it.
setInterval(() => tick(), 5000);
