import { useState } from 'react';
import {
  FONT_SIZE_PRESETS,
  getStoredFontSize,
  setStoredFontSize,
} from '../../lib/fontSize';

export function DisplaySection() {
  const [fontSize, setFontSize] = useState(() => getStoredFontSize());

  return (
    <>
      <p className="text-sm text-[var(--color-text-muted)] mb-3">
        Size of reading content (chat, journal, recipes…) — leaves the sidebar
        and buttons unchanged. For this machine only, not synced across devices,
        so a laptop and the Pocket 2 can each have their own.
      </p>
      <div className="flex flex-wrap gap-2">
        {FONT_SIZE_PRESETS.map(({ label, px }) => (
          <button
            key={px}
            onClick={() => setFontSize(setStoredFontSize(px))}
            className={`px-3 py-1.5 rounded text-sm border transition-colors ${
              fontSize === px
                ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/15 text-[var(--color-primary)]'
                : 'border-white/20 bg-white/5 hover:bg-white/10 text-[var(--color-text-muted)]'
            }`}
          >
            {label} <span className="opacity-60">{px}px</span>
          </button>
        ))}
      </div>
    </>
  );
}
