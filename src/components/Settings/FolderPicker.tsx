import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import type { BackupBrowse } from '../../hooks/api';

/**
 * Modal for choosing a directory on the machine running the backend.
 *
 * Server-side browsing rather than a native dialog or `<input webkitdirectory>`:
 * the value being chosen is a **server** path (for rsync, or for the Files
 * tab's root) and neither of those gives you one. A browser file input yields
 * file handles with no real filesystem path, and a PyWebView folder dialog
 * would only work in the desktop window — this panel also has to work over
 * the LAN in network mode.
 *
 * `browse` defaults to the backup destination's listing endpoint — it's
 * already generic (any server path in, its subdirectories out, nothing
 * backup-specific) so other pickers (Settings → Files) reuse it rather than
 * duplicating the same directory walk under a different route.
 */
export function FolderPicker({
  initialPath,
  onSelect,
  onClose,
  browse = api.backup.browse,
  title = 'Choose a backup folder',
}: {
  initialPath: string;
  onSelect: (path: string) => void;
  onClose: () => void;
  browse?: (path: string) => Promise<BackupBrowse>;
  title?: string;
}) {
  const [path, setPath] = useState(initialPath || '/');

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['folderPicker', 'browse', path],
    queryFn: () => browse(path),
    retry: false,
  });

  // Escape closes, matching every other dismissible surface in the app.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg max-h-[80vh] flex flex-col rounded-lg border border-white/10 bg-[var(--color-surface)] shadow-xl"
        onClick={e => e.stopPropagation()}
      >
        <div className="p-3 border-b border-white/10">
          <h3 className="text-sm font-medium text-[var(--color-text)] mb-2">
            {title}
          </h3>
          <code className="block text-xs text-[var(--color-text-muted)] break-all">
            {data?.path ?? path}
          </code>
        </div>

        <div className="px-3 py-2 border-b border-white/10 flex flex-wrap gap-1">
          {(data?.suggestions ?? []).map(s => (
            <button
              key={s.path}
              type="button"
              onClick={() => setPath(s.path)}
              className="px-2 py-0.5 rounded text-xs text-[var(--color-text-muted)] border border-white/10 hover:text-[var(--color-text)] hover:border-white/25"
            >
              {s.name}
            </button>
          ))}
        </div>

        <div className="flex-1 overflow-y-auto min-h-32">
          {isLoading && (
            <p className="p-3 text-xs text-[var(--color-text-muted)]">
              Loading…
            </p>
          )}
          {isError && (
            <p className="p-3 text-xs text-red-400">
              {(error as Error)?.message || 'Could not read that folder.'}
            </p>
          )}
          {data && (
            <>
              {data.parent && (
                <button
                  type="button"
                  onClick={() => setPath(data.parent!)}
                  className="w-full text-left px-3 py-1.5 text-xs text-[var(--color-text-muted)] hover:bg-white/5"
                >
                  ../
                </button>
              )}
              {data.entries.map(entry => (
                <button
                  key={entry.path}
                  type="button"
                  onClick={() => setPath(entry.path)}
                  className="w-full text-left px-3 py-1.5 text-xs text-[var(--color-text)] hover:bg-white/5 flex items-center gap-2"
                >
                  <span className="truncate">{entry.name}/</span>
                  {!entry.writable && (
                    // Surfaced per row because an unwritable destination is the
                    // failure this panel exists to catch — better to see it
                    // before choosing than in the status afterwards.
                    <span className="ml-auto shrink-0 text-[10px] text-amber-400">
                      not writable
                    </span>
                  )}
                </button>
              ))}
              {data.entries.length === 0 && (
                <p className="p-3 text-xs text-[var(--color-text-muted)]">
                  No subfolders here.
                </p>
              )}
              {data.truncated && (
                <p className="p-3 text-xs text-[var(--color-text-muted)]">
                  Too many subfolders to list — open one directly.
                </p>
              )}
            </>
          )}
        </div>

        <div className="p-3 border-t border-white/10 flex items-center gap-2">
          {data && !data.writable && (
            <span className="text-xs text-amber-400">
              This folder isn’t writable.
            </span>
          )}
          <button
            type="button"
            onClick={onClose}
            className="ml-auto px-3 py-1.5 rounded text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={!data}
            onClick={() => data && onSelect(data.path)}
            className="px-3 py-1.5 rounded text-sm bg-[var(--color-primary)]/20 text-[var(--color-primary)] border border-[var(--color-primary)]/40 hover:bg-[var(--color-primary)]/30 disabled:opacity-50"
          >
            Use this folder
          </button>
        </div>
      </div>
    </div>
  );
}
