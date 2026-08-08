import { useEffect, useState } from 'react';
import type { PracticeExplanation } from '../../hooks/api';

interface Props {
  explanation: PracticeExplanation | null;
  // A speed drill shows this while the snippet is still being typed, so it
  // starts closed and stays out of the way; a graded recall opens it, because
  // reading it is the whole payoff of having just written the thing from memory.
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
  const [open, setOpen] = useState(defaultOpen);

  // The panel outlives a drill when only the snippet inside it changes, so the
  // open state is re-synced rather than left wherever the last drill put it.
  useEffect(() => {
    setOpen(defaultOpen);
  }, [defaultOpen, explanation]);

  if (!explanation) return null;

  return (
    <div className="rounded-lg border border-white/10 bg-[var(--color-surface)]">
      <button
        onClick={() => setOpen(o => !o)}
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
