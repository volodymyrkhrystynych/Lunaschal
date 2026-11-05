import { createRunner } from './lib/archive.js';

const $ = id => document.getElementById(id);
const state = JSON.parse(sessionStorage.getItem('archive') || 'null') || {
  clientId: crypto.randomUUID(),
};
sessionStorage.setItem('archive', JSON.stringify({ clientId: state.clientId }));
let connected = false;

async function send(type, payload = {}) {
  const reply = await chrome.runtime.sendMessage({ type, ...payload });
  if (!reply?.ok)
    throw new Error(reply?.error || 'Could not contact Lunaschal.');
  return reply;
}

const runner = createRunner({
  send,
  tabs: chrome.tabs,
  debuggerApi: chrome.debugger,
  state,
  onJob: job => {
    $('status').textContent = `Printing ${job.url}…`;
  },
});

const OUTCOME = {
  ok: 'archived',
  login: 'needs a login',
  error: 'failed',
};

async function tick() {
  if (!connected) return;
  try {
    const reply = await runner.tick();
    if (!connected) return;
    $('error').textContent = '';
    if (reply) {
      const queued = reply.archiver?.queued ?? 0;
      $('status').textContent = queued
        ? `Connected. ${queued} link${queued === 1 ? '' : 's'} waiting.`
        : 'Connected. Waiting for links.';
    }
    if (state.last) {
      const { url, outcome, message } = state.last;
      $('last').textContent =
        `Last: ${url} — ${OUTCOME[outcome] || outcome}` +
        (message && outcome !== 'ok' ? ` (${message})` : '');
    }
  } catch (error) {
    $('error').textContent = error.message;
  }
}

$('settings').onclick = () => chrome.runtime.openOptionsPage();
$('connect').onclick = async () => {
  try {
    await send('archiveConnect', { clientId: state.clientId });
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
    await send('archiveDisconnect', { clientId: state.clientId });
  } catch (error) {
    $('error').textContent = error.message;
  }
  $('connect').disabled = false;
  $('disconnect').disabled = true;
  $('status').textContent = 'Disconnected. Queued links wait in Lunaschal.';
};
// An extension tab owns the timer, so an MV3 service worker suspending between
// polls cannot strand a page halfway through printing (as in ffn.js).
setInterval(() => tick(), 5000);
// Reconnect on load: the point of this tab is to be left running, and a
// reload or browser restart should not need a click to resume.
$('connect').click();
