import { useState } from 'react';
import type { PracticeExplanation } from '../../hooks/api';
import {
  getStoredExplanationOpen,
  setStoredExplanationOpen,
} from '../../lib/practicePrefs';

interface Props {
  explanation: PracticeExplanation | null;
  // Only consulted until the user first opens or closes the panel themselves —
  // after that their choice carries across every drill. A speed drill starts it
  // closed and out of the way; a graded recall starts it open, because reading
  // it is the whole payoff of having just written the thing from memory.
  defaultOpen?: boolean;
}

// Inline `code` and *emphasis* in the explanation text, rendered rather than
// printed with their markers showing. Deliberately not a markdown parser: the
// content is one sentence per field, and these two forms are all it uses.
function renderInline(text: string) {
  return text.split(/(`[^`]+`|\*[^*]+\*)/).map((chunk, i) => {
    if (chunk.startsWith('`') && chunk.endsWith('`') && chunk.length > 1) {
      return (
        <code
          key={i}
          className="px-1 rounded bg-[var(--color-bg)] font-mono text-[0.9em] text-[var(--color-primary)]"
        >
          {chunk.slice(1, -1)}
        </code>
      );
    }
    if (chunk.startsWith('*') && chunk.endsWith('*') && chunk.length > 1) {
      return (
        <em key={i} className="italic">
          {chunk.slice(1, -1)}
        </em>
      );
    }
    return chunk;
  });
}

export function ExplanationPanel({ explanation, defaultOpen = false }: Props) {
  // Read on every mount, and the drills remount per snippet — so whichever way
  // the panel was left is the way the next snippet finds it. There is
  // deliberately no effect re-syncing this to `defaultOpen`: that is precisely
  // what used to collapse the panel again on each new drill.
  const [open, setOpen] = useState(
    () => getStoredExplanationOpen() ?? defaultOpen
  );

  function toggle() {
    const next = !open;
    setOpen(next);
    setStoredExplanationOpen(next);
  }

  if (!explanation) return null;

  return (
    <div className="rounded-lg border border-white/10 bg-[var(--color-surface)]">
      <button
        onClick={toggle}
        aria-expanded={open}
        className="w-full flex items-center gap-2 px-3 py-2 text-left text-xs uppercase tracking-wide text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
      >
        <span
          aria-hidden="true"
          className={`inline-block transition-transform ${open ? 'rotate-90' : ''}`}
        >
          ›
        </span>
        What this is
      </button>

      {open && (
        <div className="flex flex-col gap-3 px-3 pb-3 text-sm text-[var(--color-text)]">
          <p>{renderInline(explanation.summary)}</p>

          {explanation.parts.length > 0 && (
            <dl className="flex flex-col gap-2">
              {explanation.parts.map(part => (
                <div key={part.name}>
                  <dt className="font-mono text-xs text-[var(--color-primary)]">
                    {part.name}
                  </dt>
                  <dd className="text-[var(--color-text-muted)]">
                    {renderInline(part.detail)}
                  </dd>
                </div>
              ))}
            </dl>
          )}

          {explanation.related && (
            <p className="text-[var(--color-text-muted)]">
              <span className="text-xs uppercase tracking-wide">See also </span>
              {renderInline(explanation.related)}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
