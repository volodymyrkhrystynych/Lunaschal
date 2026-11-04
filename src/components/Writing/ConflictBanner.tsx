interface Props {
  onKeepMine: () => void;
  onLoadLatest: () => void;
}

/** A save refused because another device changed the document first. Saving
 * pauses until one of these is chosen; either way the losing text stays in the
 * document's history. */
export function ConflictBanner({ onKeepMine, onLoadLatest }: Props) {
  return (
    <div
      role="alert"
      className="flex items-center justify-between gap-3 px-4 py-2 border-b border-white/10 bg-yellow-500/10 text-sm text-[var(--color-text)] shrink-0"
    >
      <span>This changed on another device. Your text here is not saved.</span>
      <span className="flex gap-2 shrink-0">
        <button
          onClick={onKeepMine}
          className="px-2 py-1 rounded border border-white/20 hover:text-[var(--color-primary)]"
        >
          Keep mine
        </button>
        <button
          onClick={onLoadLatest}
          className="px-2 py-1 rounded border border-white/20 hover:text-[var(--color-primary)]"
        >
          Load latest
        </button>
      </span>
    </div>
  );
}
