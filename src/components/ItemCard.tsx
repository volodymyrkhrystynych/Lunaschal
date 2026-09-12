import type { MouseEventHandler, ReactNode, Ref } from 'react';

/** Padding presets seen across the "surface-panel card" call sites: `sm` is
 * Journal's `p-3`, `md` is Fanfic's `p-4`, `compact` is Torrent's list-row
 * `px-3 py-2`. */
const PADDING_CLASS: Record<'sm' | 'md' | 'compact', string> = {
  sm: 'p-3',
  md: 'p-4',
  compact: 'px-3 py-2',
};

/** Background presets: `muted` is the half-opacity surface most journal cards
 * use, `solid` is Fanfic's full-opacity one, `none` is Torrent's plain
 * bordered row (its background comes entirely from `borderClassName`, since
 * selection there swaps bg and border together). */
const SURFACE_CLASS: Record<'muted' | 'solid' | 'none', string> = {
  muted: 'bg-[var(--color-surface)]/50',
  solid: 'bg-[var(--color-surface)]',
  none: '',
};

const RADIUS_CLASS: Record<'md' | 'lg', string> = {
  md: 'rounded',
  lg: 'rounded-lg',
};

export interface ItemCardProps {
  /** Render as an `<li>` for a list context (Torrent) instead of a `<div>`. */
  as?: 'div' | 'li';
  /** Left-of-content image/cover. Sits beside the whole card, not just the
   * title row — matching Fanfic's cover-plus-column layout. */
  thumbnail?: ReactNode;
  /** The card's title. When `meta`/`titleActions` are both omitted, this
   * renders bare with no extra wrapper margin, so a title that already
   * manages its own header row (e.g. a clickable button spanning title+meta)
   * is not double-spaced. */
  title: ReactNode;
  /** Trailing buttons on the title row, next to the title. */
  titleActions?: ReactNode;
  /** A short trailing text line on the title row (day label, status, count).
   * Renders alongside `titleActions` on the row's right side. */
  meta?: ReactNode;
  /** A progress bar (or similar) between the header and the body. */
  progress?: ReactNode;
  /** Freeform content below the header/progress. */
  body?: ReactNode;
  /** A trailing action-button row at the bottom of the card. */
  actions?: ReactNode;
  className?: string;
  onClick?: MouseEventHandler;
  /** Ref to the card's own root element — e.g. for `scrollIntoView` on the
   * whole card when it becomes selected, rather than on one slot's content. */
  cardRef?: Ref<HTMLElement>;
  padding?: 'sm' | 'md' | 'compact';
  surface?: 'muted' | 'solid' | 'none';
  radius?: 'md' | 'lg';
  /** Full border-color (and, for stateful rows, background/hover) utility
   * classes — a single string rather than a color-only knob, since a
   * selected Torrent row swaps its background along with its border. */
  borderClassName?: string;
}

/** The "surface-panel card" shape repeated across Journal's feed cards,
 * Fanfic's library cards, and Torrent's list rows: a bordered box with a
 * title, an optional trailing meta/action row next to it, an optional
 * progress bar, freeform body content, and a bottom action-button row. Pure
 * chrome and layout — every slot keeps its own typography, exactly as each
 * call site had it before this was pulled out from under them. */
export function ItemCard({
  as = 'div',
  thumbnail,
  title,
  titleActions,
  meta,
  progress,
  body,
  actions,
  className,
  onClick,
  cardRef,
  padding = 'sm',
  surface = 'muted',
  radius = 'lg',
  borderClassName = 'border-white/5',
}: ItemCardProps) {
  const Tag = as;
  const wrapperClassName = [
    RADIUS_CLASS[radius],
    'border',
    borderClassName,
    PADDING_CLASS[padding],
    SURFACE_CLASS[surface],
    className,
  ]
    .filter(Boolean)
    .join(' ');

  const header =
    meta || titleActions ? (
      <div className="flex flex-wrap items-start justify-between gap-2 mb-2">
        <div className="min-w-0">{title}</div>
        <div className="flex flex-wrap items-center gap-2 shrink-0">
          {meta}
          {titleActions}
        </div>
      </div>
    ) : (
      title
    );

  const content = (
    <>
      {header}
      {progress}
      {body}
      {actions}
    </>
  );

  // `as` picks between two intrinsic tags with incompatible ref/prop types;
  // TS can't narrow a dynamically-chosen tag, so this one JSX call is cast.
  const Wrapper = Tag as 'div';
  return (
    <Wrapper
      className={wrapperClassName}
      onClick={onClick}
      ref={cardRef as Ref<HTMLDivElement>}
    >
      {thumbnail ? (
        <div className="flex items-start gap-3">
          {thumbnail}
          <div className="flex-1 min-w-0">{content}</div>
        </div>
      ) : (
        content
      )}
    </Wrapper>
  );
}
