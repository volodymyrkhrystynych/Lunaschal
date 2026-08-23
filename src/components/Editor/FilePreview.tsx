import { useQuery } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { formatBytes } from '../../lib/journalAttachments';
import { previewKind } from '../../lib/filePreview';

/**
 * Detail-pane view for a non-text file — everything EditorPane's CodeMirror
 * can't (and shouldn't try to) open. Renders media inline for the cloud-drive
 * feel; anything else falls back to a name/size card with a download link.
 *
 * Size comes from the parent directory's listing rather than a dedicated
 * lookup — FileTree already fetched it to render the tree (same
 * `['files','list',dir]` query key as `useFileTree`), so this is normally a
 * cache hit, not an extra request.
 */
export function FilePreview({ filePath }: { filePath: string }) {
  const name = filePath.includes('/')
    ? filePath.slice(filePath.lastIndexOf('/') + 1)
    : filePath;
  const dir = filePath.includes('/')
    ? filePath.slice(0, filePath.lastIndexOf('/'))
    : '';

  const { data: siblings } = useQuery({
    queryKey: ['files', 'list', dir],
    queryFn: () => api.files.list(dir || undefined),
  });
  const size = siblings?.find(e => e.path === filePath)?.size ?? null;

  const src = api.files.contentUrl(filePath);
  const kind = previewKind(filePath);

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="flex items-center justify-between px-3 py-1 border-b border-white/10 bg-[var(--color-surface)] shrink-0">
        <span className="text-sm text-[var(--color-text-muted)] truncate">
          {filePath}
        </span>
        <a
          href={api.files.contentUrl(filePath, true)}
          className="text-xs text-[var(--color-primary)] hover:underline shrink-0 ml-2"
        >
          Download
        </a>
      </div>

      <div className="flex-1 overflow-auto flex items-center justify-center p-4">
        {kind === 'image' && (
          <img
            src={src}
            alt={name}
            className="max-w-full max-h-full object-contain"
          />
        )}
        {kind === 'video' && (
          <video src={src} controls className="max-w-full max-h-full" />
        )}
        {kind === 'audio' && <audio src={src} controls className="w-full" />}
        {kind === 'pdf' && (
          <iframe src={src} title={name} className="w-full h-full" />
        )}
        {kind === 'other' && (
          <div className="text-center text-[var(--color-text-muted)]">
            <p className="text-sm text-[var(--color-text)] mb-1">{name}</p>
            {size != null && (
              <p className="text-xs mb-3">{formatBytes(size)}</p>
            )}
            <a
              href={api.files.contentUrl(filePath, true)}
              className="inline-block px-3 py-1.5 rounded text-sm bg-[var(--color-primary)]/20 text-[var(--color-primary)] border border-[var(--color-primary)]/40 hover:bg-[var(--color-primary)]/30"
            >
              Download
            </a>
          </div>
        )}
      </div>
    </div>
  );
}
