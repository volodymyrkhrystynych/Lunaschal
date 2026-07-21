import { useQuery } from '@tanstack/react-query';
import { api, type FileEntry } from '../../hooks/api';
import { MessageMarkdown } from '../MessageMarkdown';

interface Props {
  entry: FileEntry | null;
}

/**
 * Read-only preview of the tree's highlighted node, shown while no file is open
 * for editing: rendered markdown for a file, a text listing of children for a
 * folder. Opening a file (Enter/click) swaps this out for the editor.
 */
export function NotebookPreviewPane({ entry }: Props) {
  const isDir = entry?.isDir ?? false;

  const fileQuery = useQuery({
    queryKey: ['notebook', 'files', 'read', entry?.path],
    queryFn: () => api.notebook.files.read(entry!.path),
    enabled: !!entry && !isDir,
  });

  const dirQuery = useQuery({
    queryKey: ['notebook', 'files', 'list', entry?.path],
    queryFn: () => api.notebook.files.list(entry!.path || undefined),
    enabled: !!entry && isDir,
  });

  if (!entry) {
    return (
      <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)]">
        Select a file to edit
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-1 border-b border-white/10 bg-[var(--color-surface)] shrink-0">
        <span className="text-sm text-[var(--color-text-muted)] truncate">
          {entry.path}
        </span>
        <span className="text-xs text-[var(--color-text-muted)]/60 uppercase tracking-wide shrink-0">
          {isDir ? 'Folder' : 'Preview'}
        </span>
      </div>

      <div className="flex-1 overflow-y-auto p-4 text-sm text-[var(--color-text)]">
        {isDir ? (
          <FolderListing
            entry={entry}
            children={dirQuery.data}
            isLoading={dirQuery.isLoading}
          />
        ) : fileQuery.isLoading ? (
          <span className="text-[var(--color-text-muted)]">Loading…</span>
        ) : fileQuery.isError ? (
          <span className="text-[var(--color-text-muted)]">
            Can't preview this file.
          </span>
        ) : fileQuery.data?.content.trim() ? (
          <MessageMarkdown content={fileQuery.data.content} />
        ) : (
          <span className="text-[var(--color-text-muted)] italic">
            Empty file.
          </span>
        )}
      </div>
    </div>
  );
}

function FolderListing({
  entry,
  children,
  isLoading,
}: {
  entry: FileEntry;
  children: FileEntry[] | undefined;
  isLoading: boolean;
}) {
  if (isLoading) {
    return <span className="text-[var(--color-text-muted)]">Loading…</span>;
  }
  if (!children || children.length === 0) {
    return (
      <span className="text-[var(--color-text-muted)] italic">
        Empty folder.
      </span>
    );
  }
  return (
    <div>
      <div className="text-[var(--color-text-muted)] mb-2">
        {children.length} item{children.length === 1 ? '' : 's'} in {entry.name}
      </div>
      <ul className="space-y-0.5 font-mono text-[13px]">
        {children.map(child => (
          <li key={child.path} className="flex items-center gap-2">
            <span className="text-[var(--color-text-muted)] w-3 shrink-0">
              {child.isDir ? '▸' : '·'}
            </span>
            <span className="truncate">
              {child.name}
              {child.isDir ? '/' : ''}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
