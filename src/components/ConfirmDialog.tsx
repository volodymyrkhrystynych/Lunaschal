import { useEffect } from 'react';

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
  /** Styles the confirm button as destructive (red) rather than a neutral action. */
  danger?: boolean;
}

/**
 * Shared replacement for `window.confirm(...)`. Native confirm is synchronous
 * and blocks the whole tab; this is a modal + local state, so every call site
 * needs an `open` flag and an `onConfirm`/`onCancel` pair instead of a boolean
 * return value. Follows the same `fixed inset-0` backdrop + centered panel
 * convention as `Ideas/SketchPicker.tsx` and `Settings/FolderPicker.tsx`.
 */
export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel,
  cancelLabel = 'Cancel',
  onConfirm,
  onCancel,
  danger = false,
}: ConfirmDialogProps) {
  // Escape cancels, matching FolderPicker and every other dismissible surface.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onCancel}
    >
      <div
        className="w-full max-w-sm rounded-lg border border-white/10 bg-[var(--color-surface)] shadow-xl"
        onClick={e => e.stopPropagation()}
      >
        <div className="p-4">
          <h2 className="text-sm font-medium text-[var(--color-text)]">
            {title}
          </h2>
          {message && (
            <p className="mt-1.5 text-sm text-[var(--color-text-muted)]">
              {message}
            </p>
          )}
        </div>
        <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-white/10">
          <button
            type="button"
            onClick={onCancel}
            className="px-3 py-1.5 rounded text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            autoFocus
            className={
              danger
                ? 'px-3 py-1.5 rounded text-sm bg-red-500/20 text-red-400 border border-red-500/40 hover:bg-red-500/30'
                : 'px-3 py-1.5 rounded text-sm bg-[var(--color-primary)]/20 text-[var(--color-primary)] border border-[var(--color-primary)]/40 hover:bg-[var(--color-primary)]/30'
            }
          >
            {confirmLabel ?? (danger ? 'Delete' : 'Confirm')}
          </button>
        </div>
      </div>
    </div>
  );
}
