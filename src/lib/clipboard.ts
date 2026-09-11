/**
 * Copy text in both regular browsers and embedded webviews.
 *
 * The async Clipboard API is unavailable or permission-gated in some
 * PyWebView/insecure-context setups. `execCommand` is deprecated, but remains
 * the useful compatibility path for those environments.
 */
export async function copyText(text: string): Promise<void> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
  } catch {
    // Try the compatibility path below.
  }

  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'fixed';
  textarea.style.opacity = '0';
  document.body.appendChild(textarea);
  textarea.select();

  let copied = false;
  try {
    copied =
      typeof document.execCommand === 'function' &&
      document.execCommand('copy');
  } finally {
    textarea.remove();
  }

  if (!copied) {
    throw new Error('Clipboard is unavailable');
  }
}
