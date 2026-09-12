/**
 * Shared loading/empty/error primitives.
 *
 * Every view used to roll its own of these three from raw fetch/React-Query
 * state, with inconsistent wording, inconsistent `role` usage, and small
 * visual drift (a `<p>` here, a bordered `<div>` there). See
 * docs/ui-consolidation.md section 4. Nothing here is feature-specific —
 * callers keep their own copy, these just standardize the shape it sits in.
 */

/** `…` over `...`: the app's copy already leans that way (Meetings' phase
 * labels, most "Saving…"/"Loading more…" strings), so this is the default a
 * caller falls back to rather than a new convention. */
export function LoadingState({
  label = 'Loading…',
  variant = 'inline',
}: {
  label?: string;
  variant?: 'inline' | 'panel';
}) {
  if (variant === 'panel') {
    return (
      <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)]">
        {label}
      </div>
    );
  }
  return <div className="text-[var(--color-text-muted)]">{label}</div>;
}

export function EmptyState({
  title,
  message,
  action,
}: {
  title: string;
  message?: string;
  action?: { label: string; onClick: () => void };
}) {
  return (
    <div className="text-center text-[var(--color-text-muted)] py-12">
      <div className="text-[var(--color-text)]">{title}</div>
      {message && <div className="mt-2 text-sm">{message}</div>}
      {action && (
        <button
          onClick={action.onClick}
          className="mt-4 px-4 py-2 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 transition-colors"
        >
          {action.label}
        </button>
      )}
    </div>
  );
}

/** `Error` and plain strings are the common cases (a thrown fetch, a
 * hand-raised message); anything else is a shape a caller shouldn't have to
 * guess how to render, so it gets a flat fallback rather than `[object
 * Object]` or a `JSON.stringify` dump. */
function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === 'string') return error;
  return 'Something went wrong.';
}

export function ErrorBanner({
  error,
  onRetry,
  role = 'alert',
}: {
  error: unknown;
  onRetry?: () => void;
  role?: 'alert' | 'status';
}) {
  return (
    <div
      role={role}
      className="px-3 py-2 bg-red-500/10 border border-red-500/20 rounded text-sm text-red-400 flex items-center gap-2"
    >
      <span className="flex-1">{errorMessage(error)}</span>
      {onRetry && (
        <button
          onClick={onRetry}
          className="shrink-0 underline hover:text-red-300"
        >
          Retry
        </button>
      )}
    </div>
  );
}
