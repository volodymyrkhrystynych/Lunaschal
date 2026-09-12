import { useEffect } from 'react';
import { createPortal } from 'react-dom';

/**
 * Shared overlay shell: full-screen backdrop + a panel that stops its own
 * clicks from reaching the backdrop, plus Escape-to-close. Four call sites
 * (SketchPicker, FolderPicker, BookmarkMenu, NoteReview) hand-rolled this
 * exact `fixed inset-0 z-50` + `stopPropagation` pattern with small drift —
 * this is the one copy. Each caller keeps its own header/body/footer markup
 * as `children`; only the backdrop/panel wrapper is shared.
 *
 * Rendered through a portal so callers never have to think about where in
 * the tree they're mounted — BookmarkMenu already needed this for iPad, the
 * other three get it for free.
 *
 * Sidebar's mobile drawer is a different animation/positioning (a pinned
 * off-canvas panel, not a centered dialog) and deliberately doesn't use this.
 */
export function Modal({
  onClose,
  children,
  className = '',
  backdropClassName = 'bg-black/70',
  closeOnBackdropClick = true,
  closeOnEscape = true,
  role,
  ariaLabel,
}: {
  onClose: () => void;
  children: React.ReactNode;
  /** Classes for the inner panel. Panel shape varies a lot between callers
   *  (max-width, flex layout, portal-only menu sizing), so this has no
   *  default — pass the caller's existing panel classes verbatim. */
  className?: string;
  /** Classes for the full-screen backdrop, minus the fixed positioning
   *  and flex-centering, which are always applied. */
  backdropClassName?: string;
  /** BookmarkMenu wants no click-away dismiss so it can't be nudged off
   *  screen by an accidental tap. */
  closeOnBackdropClick?: boolean;
  closeOnEscape?: boolean;
  role?: string;
  ariaLabel?: string;
}) {
  useEffect(() => {
    if (!closeOnEscape) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [closeOnEscape, onClose]);

  return createPortal(
    <div
      role={role}
      aria-label={ariaLabel}
      className={`fixed inset-0 z-50 flex items-center justify-center p-4 ${backdropClassName}`}
      onClick={closeOnBackdropClick ? onClose : undefined}
    >
      <div onClick={e => e.stopPropagation()} className={className}>
        {children}
      </div>
    </div>,
    document.body
  );
}
