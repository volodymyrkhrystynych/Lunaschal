import { useEffect, useRef, useState } from 'react';

interface CollapsibleSectionProps {
  /** Required unless `hideHeader` suppresses the built-in trigger button. */
  title?: string;
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
  /**
   * Controlled mode: the parent owns the open/closed flag (e.g. a recipe list
   * that keeps only one row open at a time). Falls back to internal state when
   * omitted, which is what every plain Settings group still uses.
   */
  open?: boolean;
  onToggle?: (open: boolean) => void;
  /**
   * Suppress the built-in chevron+title button entirely — for a caller whose
   * own trigger lives elsewhere in a row of other controls (a fic card's
   * "Details" toggle sits beside Review/Update/Delete buttons; a recipe's
   * title text doubles as its own expand toggle). Requires `open`/`onToggle`,
   * since there is no button left here to flip internal state.
   */
  hideHeader?: boolean;
  /** Override the default surface-box styling of the expanded content. */
  bodyClassName?: string;
  /** Override the outer wrapper's className (default `mb-4`). */
  sectionClassName?: string;
}

const DEFAULT_BODY_CLASS =
  'p-4 bg-[var(--color-surface)] rounded-lg border border-white/10';

export function CollapsibleSection({
  title,
  children,
  defaultExpanded = false,
  autoExpand = false,
  open,
  onToggle,
  hideHeader = false,
  bodyClassName = DEFAULT_BODY_CLASS,
  sectionClassName = 'mb-4',
}: CollapsibleSectionProps) {
  const [internalExpanded, setInternalExpanded] = useState(defaultExpanded);
  const expanded = open ?? internalExpanded;
  const setExpanded = onToggle ?? setInternalExpanded;
  const autoExpanded = useRef(false);

  useEffect(() => {
    if (autoExpand && !autoExpanded.current) {
      autoExpanded.current = true;
      setExpanded(true);
    }
    // setExpanded is either the caller's onToggle or the stable setState
    // dispatcher — including it would refire this on every controlled render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoExpand]);

  return (
    <section className={sectionClassName}>
      {!hideHeader && (
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
      )}
      <div
        className={`overflow-hidden transition-[max-height] duration-200 ease-in-out ${
          expanded ? `max-h-[4000px]${hideHeader ? '' : ' mt-4'}` : 'max-h-0'
        }`}
      >
        <div className={bodyClassName}>{children}</div>
      </div>
    </section>
  );
}
