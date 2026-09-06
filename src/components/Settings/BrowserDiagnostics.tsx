import { useState } from 'react';
import { readBrowserDiagnostics } from '@/lib/browserDiagnostics';

export function BrowserDiagnostics() {
  const [entries, setEntries] = useState(readBrowserDiagnostics);
  return (
    <details className="rounded border border-white/10 p-3">
      <summary className="cursor-pointer">Browser reload history</summary>
      <p className="text-sm my-2 text-[var(--color-text-muted)]">
        Recent page loads and app resets in this tab. Stays on this device; does
        not include your text. A new boot identifier means the page loaded
        again. A shell mount with the same identifier means the UI restarted.
        These signals may narrow the cause; browsers do not always report why
        they reload a page.
      </p>
      <button
        type="button"
        onClick={() => setEntries(readBrowserDiagnostics())}
        className="text-[var(--color-primary)]"
      >
        Refresh browser history
      </button>
      <textarea
        readOnly
        aria-label="Browser reload history"
        rows={8}
        className="mt-2 w-full bg-[var(--color-bg)] text-xs font-mono"
        value={JSON.stringify(entries, null, 2)}
      />
    </details>
  );
}
