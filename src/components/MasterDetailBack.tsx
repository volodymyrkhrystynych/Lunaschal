interface Props {
  onClick: () => void;
  /** What the user returns to, e.g. "Files", "Chapters", "Chats". */
  label: string;
}

/**
 * Mobile-only "back to the list" affordance shown at the top of a detail pane in
 * a master-detail view. Hidden on desktop (md:hidden) where both panes coexist.
 */
export function MasterDetailBack({ onClick, label }: Props) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="md:hidden shrink-0 flex items-center gap-1 px-3 min-h-[44px] text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] border-b border-white/10 bg-[var(--color-surface)]"
    >
      <span aria-hidden="true">←</span>
      <span>{label}</span>
    </button>
  );
}
