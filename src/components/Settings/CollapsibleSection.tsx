import { useEffect, useRef, useState } from 'react';

interface CollapsibleSectionProps {
  title: string;
  children: React.ReactNode;
  /**
   * Settings groups are collapsed by default — the General tab holds fifteen
   * of them, and expanded-by-default meant the page opened as a wall of
   * controls you had to scroll past to reach the one you wanted.
   */
  defaultExpanded?: boolean;
  /**
   * Open this section once, when the flag first becomes true, for a group that
   * has something wrong with it.
   *
   * The flag usually arrives after mount (it depends on a fetch), which is why
   * this cannot just feed `defaultExpanded` — by the time the answer is known,
   * useState has already committed to the initial value. It fires only on the
   * first transition, so a user who collapses the section again is not fought
   * with on the next poll.
   */
  autoExpand?: boolean;
}

export function CollapsibleSection({
  title,
  children,
  defaultExpanded = false,
  autoExpand = false,
}: CollapsibleSectionProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const autoExpanded = useRef(false);

  useEffect(() => {
    if (autoExpand && !autoExpanded.current) {
      autoExpanded.current = true;
      setExpanded(true);
    }
  }, [autoExpand]);

  return (
    <section className="mb-4">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
        className="flex items-center gap-2 w-full text-left"
      >
        <span className="w-4 h-4 shrink-0 text-[var(--color-text-muted)]">
          {expanded ? '▾' : '▸'}
        </span>
        <h2 className="text-lg font-medium text-[var(--color-text)]">
          {title}
        </h2>
      </button>
      <div
        className={`overflow-hidden transition-[max-height] duration-200 ease-in-out ${
          expanded ? 'max-h-[4000px] mt-4' : 'max-h-0'
        }`}
      >
        <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
          {children}
        </div>
      </div>
    </section>
  );
}
