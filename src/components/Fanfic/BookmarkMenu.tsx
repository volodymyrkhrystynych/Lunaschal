import { createPortal } from 'react-dom';

/**
 * Centered bookmark menu. No click-away dismiss — only the ✕ button or a
 * selection closes it — so it stays exactly where the OS places it on iPad/iPhone
 * without getting pushed around or dismissed on an accidental tap.
 */
export function BookmarkMenu({
  onPick,
  onClose,
}: {
  onPick: (type: 'favorite' | 'continue') => void;
  onClose: () => void;
}) {
  const pick = (type: 'favorite' | 'continue') => {
    onPick(type);
    onClose();
  };

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <div className="min-w-44 p-1 bg-[var(--color-surface)] border border-white/15 rounded-lg shadow-xl">
        <div className="flex items-center justify-between px-3 py-2 border-b border-white/10">
          <span className="text-sm font-medium text-[var(--color-text)]">
            Bookmark chapter
          </span>
          <button
            onClick={onClose}
            className="text-[var(--color-text-muted)] hover:text-[var(--color-text)] text-sm"
          >
            ✕
          </button>
        </div>
        <div className="p-1">
          <button
            onClick={() => pick('favorite')}
            className="w-full text-left px-3 py-1.5 text-sm text-[var(--color-text)] rounded hover:bg-white/10"
          >
            ★ Favorite
          </button>
          <button
            onClick={() => pick('continue')}
            className="w-full text-left px-3 py-1.5 text-sm text-[var(--color-text)] rounded hover:bg-white/10"
          >
            ▶ Continue reading here
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
