const $ = id => document.getElementById(id);

const DEFAULT_BASE_URL = 'http://localhost:5000';

chrome.storage.local.get(['baseUrl', 'password']).then(stored => {
  $('baseUrl').value = stored.baseUrl || DEFAULT_BASE_URL;
  $('password').value = stored.password || '';
});

$('save').addEventListener('click', async () => {
  const baseUrl = ($('baseUrl').value || DEFAULT_BASE_URL)
    .trim()
    .replace(/\/+$/, '');
  const password = $('password').value;

  // A non-default host is not in `host_permissions`, so the service worker's
  // fetch would fail with an opaque network error. Ask for it now, while there
  // is a user gesture to hang the prompt on.
  try {
    const origin = new URL(baseUrl).origin;
    const isDefault = /^https?:\/\/(localhost|127\.0\.0\.1):5000$/.test(origin);
    if (!isDefault) {
      const granted = await chrome.permissions.request({
        origins: [`${origin}/*`],
      });
      if (!granted) {
        $('saved').textContent =
          'Not saved — permission for that address was declined';
        $('saved').hidden = false;
        return;
      }
    }
  } catch {
    $('saved').textContent = 'That does not look like a URL';
    $('saved').hidden = false;
    return;
  }

  await chrome.storage.local.set({ baseUrl, password });
  $('saved').textContent = 'Saved';
  $('saved').hidden = false;
  setTimeout(() => {
    $('saved').hidden = true;
  }, 1500);
});
