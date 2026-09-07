import { useEffect, useState } from 'react';

/**
 * A block of AI-written text that opens the first time it is seen and stays
 * collapsed on every later load.
 *
 * The rule is the point: a caption or a transcript is worth reading once, when
 * it arrives, and is clutter above the thing it describes forever after. So
 * "seen" is remembered per block in `localStorage` rather than the block
 * defaulting one way for everything.
 *
 * Extracted from `JournalAttachments`' own `AttachmentDescription` when the
 * food log grew transcripts of its own — the second copy of a `<details>` with
 * a storage key is where the two would start disagreeing about which of them
 * opens.
 */
export function CollapsibleText({
  storageKey,
  label,
  children,
}: {
  /** Unique per block; what "already seen" is remembered against. */
  storageKey: string;
  label: string;
  children: string;
}) {
  const [isOpen, setIsOpen] = useState(() => {
    try {
      return localStorage.getItem(storageKey) !== 'true';
    } catch {
      // Storage can be unavailable in private/restricted browser contexts. In
      // that case, prefer showing the text instead of hiding something new.
      return true;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(storageKey, 'true');
    } catch {
      // Persistence is a convenience; the text remains usable without it.
    }
  }, [storageKey]);

  return (
    <details
      open={isOpen}
      onToggle={event => setIsOpen(event.currentTarget.open)}
      className="bg-white/5 rounded text-sm text-[var(--color-text-muted)]"
    >
      <summary className="cursor-pointer select-none px-3 py-2">
        {label}
      </summary>
      <div className="px-3 pb-2 whitespace-pre-wrap italic">{children}</div>
    </details>
  );
}
