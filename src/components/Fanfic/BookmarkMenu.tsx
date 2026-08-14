import { useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

/**
 * Small context menu opened by right-click (desktop) or long-press (mobile —
 * touch browsers already dispatch `contextmenu` for a long-press, so the
 * reader wires both gestures to a single `onContextMenu` handler and just
 * passes the event's coordinates here). Same portal + fixed-overlay
 * click-away pattern as `FolderPicker` in `Folders.tsx`.
 */
export function BookmarkMenu({
  x,
  y,
  onPick,
  onClose,
}: {
  x: number;
  y: number;
  onPick: (type: 'favorite' | 'continue') => void;
  onClose: () => void;
}) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  useLayoutEffect(() => {
    const menu = menuRef.current;
    if (!menu) return;
    const margin = 8;
    const left = Math.min(x, window.innerWidth - menu.offsetWidth - margin);
    const top = Math.min(y, window.innerHeight - menu.offsetHeight - margin);
    setPos({ top: Math.max(margin, top), left: Math.max(margin, left) });
  }, [x, y]);

  const pick = (type: 'favorite' | 'continue') => {
    onPick(type);
    onClose();
  };

  return createPortal(
    <>
      <div className="fixed inset-0 z-40" onClick={onClose} />
      <div
        ref={menuRef}
        className="fixed z-50 min-w-44 p-1 bg-[var(--color-surface)] border border-white/15 rounded-lg shadow-xl"
        style={{
          top: pos?.top ?? 0,
          left: pos?.left ?? 0,
          visibility: pos ? 'visible' : 'hidden',
        }}
      >
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
    </>,
    document.body
  );
}
