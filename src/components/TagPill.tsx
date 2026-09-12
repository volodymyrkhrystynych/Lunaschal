import type { ReactNode } from 'react';

const DEFAULT_ACTIVE =
  'border-[var(--color-primary)] bg-[var(--color-primary)]/20 text-[var(--color-primary)]';
const DEFAULT_INACTIVE =
  'border-white/20 text-[var(--color-text-muted)] hover:border-white/40 hover:text-[var(--color-text)]';

/**
 * The rounded, active/inactive class-toggle filter-pill button reimplemented
 * byte-for-byte across Journal, Food, Fanfic, and Learning before this
 * extraction (docs/ui-consolidation.md, section 1). Defaults match Journal's
 * curated tag filter buttons, the reference implementation; `size` and the
 * `activeClassName`/`inactiveClassName` overrides exist because a few call
 * sites genuinely diverge (Fanfic/Learning use a larger `text-sm` and their
 * own active-state colors) rather than being copy-paste drift to erase.
 * Presentational only — callers own the active/onClick state.
 */
export function TagPill({
  active,
  onClick,
  label,
  children,
  count,
  prefix,
  title,
  size = 'xs',
  activeClassName,
  inactiveClassName,
  className = '',
}: {
  active: boolean;
  onClick: () => void;
  label?: ReactNode;
  children?: ReactNode;
  count?: number;
  prefix?: string;
  title?: string;
  size?: 'xs' | 'sm';
  activeClassName?: string;
  inactiveClassName?: string;
  className?: string;
}) {
  const textSize = size === 'sm' ? 'text-sm' : 'text-xs';
  const stateClass = active
    ? (activeClassName ?? DEFAULT_ACTIVE)
    : (inactiveClassName ?? DEFAULT_INACTIVE);

  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className={`px-3 py-1 ${textSize} rounded-full border transition-colors ${stateClass} ${className}`}
    >
      {prefix}
      {label ?? children}
      {count !== undefined && (
        <span className="ml-1 opacity-60">({count})</span>
      )}
    </button>
  );
}
