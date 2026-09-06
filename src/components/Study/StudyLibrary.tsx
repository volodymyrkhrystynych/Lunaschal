import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import {
  offlineReason,
  sourceSubtitle,
  type StudySource,
} from '../../lib/study';

const KIND_ICON: Record<StudySource['kind'], string> = {
  pdf: '📕',
  web: '🌐',
  youtube: '🎬',
};

interface Props {
  onOpen: (source: StudySource) => void;
  /**
   * Whether a row leads anywhere. False below 1024px, where this is the whole
   * of Study: an import queue you fill from the phone you found the link on,
   * to be read later on a screen that fits the desk (Study.tsx).
   */
  canOpen?: boolean;
}

export function StudyLibrary({ onOpen, canOpen = true }: Props) {
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [urlKind, setUrlKind] = useState<'web' | 'youtube' | null>(null);
  const [url, setUrl] = useState('');
  const [uploadError, setUploadError] = useState<string | null>(null);

  const { data: sources = [], isLoading } = useQuery({
    queryKey: ['study', 'sources'],
    queryFn: api.study.sources,
    // Same reasoning as the desk: poll only while something is importing.
    refetchInterval: q =>
      (q.state.data ?? []).some(s => s.importStatus === 'importing')
        ? 2000
        : false,
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ['study', 'sources'] });

  const uploadPdf = useMutation({
    mutationFn: (file: File) => api.study.uploadPdf(file),
    onSuccess: invalidate,
    onError: (e: unknown) =>
      setUploadError(e instanceof Error ? e.message : 'Upload failed.'),
  });

  const importUrl = useMutation({
    mutationFn: async ({
      kind,
      value,
      replaces,
    }: {
      kind: 'web' | 'youtube';
      value: string;
      /** A failed row this import supersedes — Retry, rather than a new one. */
      replaces?: string;
    }) => {
      const started =
        kind === 'web'
          ? await api.study.importWeb(value)
          : await api.study.importYoutube(value);
      // Dropped only once the replacement is under way, so a Retry that is
      // refused outright still leaves the original row and its error visible.
      if (replaces) await api.study.remove(replaces);
      return started;
    },
    onSuccess: () => {
      setUrl('');
      setUrlKind(null);
      void invalidate();
    },
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.study.remove(id),
    onSuccess: invalidate,
  });

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="shrink-0 flex flex-wrap items-center gap-2 px-4 py-3 border-b border-white/10 bg-[var(--color-surface)]">
        <h2 className="font-semibold text-[var(--color-text)] mr-2">Study</h2>
        <input
          ref={fileRef}
          type="file"
          accept="application/pdf,.pdf"
          className="hidden"
          onChange={e => {
            const file = e.target.files?.[0];
            e.target.value = '';
            if (!file) return;
            setUploadError(null);
            uploadPdf.mutate(file);
          }}
        />
        <ToolbarButton
          onClick={() => fileRef.current?.click()}
          busy={uploadPdf.isPending}
        >
          📕 Upload PDF
        </ToolbarButton>
        <ToolbarButton onClick={() => setUrlKind('web')}>
          🌐 Import website
        </ToolbarButton>
        <ToolbarButton onClick={() => setUrlKind('youtube')}>
          🎬 Import YouTube
        </ToolbarButton>
      </div>

      {urlKind && (
        <div className="shrink-0 flex items-center gap-2 px-4 py-2 border-b border-white/10">
          <input
            autoFocus
            value={url}
            onChange={e => setUrl(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter' && url.trim()) {
                importUrl.mutate({ kind: urlKind, value: url.trim() });
              }
              if (e.key === 'Escape') setUrlKind(null);
            }}
            placeholder={
              urlKind === 'web'
                ? 'https://example.com/article'
                : 'https://www.youtube.com/watch?v=…'
            }
            aria-label={urlKind === 'web' ? 'Website URL' : 'YouTube URL'}
            className="flex-1 px-2 py-1 text-sm rounded bg-[var(--color-bg)] border border-white/10 text-[var(--color-text)] outline-none"
          />
          <ToolbarButton
            onClick={() =>
              url.trim() &&
              importUrl.mutate({ kind: urlKind, value: url.trim() })
            }
            busy={importUrl.isPending}
          >
            Import
          </ToolbarButton>
          <ToolbarButton onClick={() => setUrlKind(null)}>Cancel</ToolbarButton>
        </div>
      )}

      {uploadError && (
        <div className="shrink-0 px-4 py-2 text-sm text-amber-400">
          {uploadError}
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-4">
        {isLoading ? (
          <div className="text-[var(--color-text-muted)]">Loading…</div>
        ) : sources.length === 0 ? (
          <div className="text-[var(--color-text-muted)] max-w-prose">
            {`Nothing here yet. Upload a book, archive a web page, or pull down a YouTube video — ${
              canOpen
                ? 'then it opens beside a notebook page you write in.'
                : 'then read it at the desk, on a screen wide enough for two panes.'
            }`}
          </div>
        ) : (
          <ul className="flex flex-col gap-1">
            {sources.map(source => (
              <li key={source.id}>
                <div className="w-full flex items-center gap-3 px-3 py-2 rounded hover:bg-white/5 group">
                  {/* A plain div rather than a disabled button where there is
                   * nowhere to go: a button that cannot be pressed still
                   * reads as one, and on a phone every row would be it. */}
                  <RowBody
                    onOpen={canOpen ? () => onOpen(source) : undefined}
                    ready={source.importStatus === 'ready'}
                  >
                    <span className="text-lg shrink-0">
                      {KIND_ICON[source.kind]}
                    </span>
                    <span className="min-w-0">
                      <span className="block truncate text-[var(--color-text)]">
                        {source.title || 'Untitled'}
                      </span>
                      <span className="block truncate text-xs text-[var(--color-text-muted)]">
                        {source.importStatus === 'importing'
                          ? `Importing… ${source.importProgress?.phase ?? ''}`.trim()
                          : source.importStatus === 'error'
                            ? (source.importError ?? 'Import failed')
                            : source.fileAvailable === false
                              ? offlineReason(source)
                              : sourceSubtitle(source)}
                      </span>
                    </span>
                  </RowBody>
                  {source.importStatus === 'error' && source.sourceUrl && (
                    <ToolbarButton
                      onClick={() =>
                        importUrl.mutate({
                          kind: source.kind === 'youtube' ? 'youtube' : 'web',
                          value: source.sourceUrl as string,
                          replaces: source.id,
                        })
                      }
                    >
                      Retry
                    </ToolbarButton>
                  )}
                  <button
                    type="button"
                    onClick={() => remove.mutate(source.id)}
                    aria-label={`Delete ${source.title || 'source'}`}
                    className="px-2 py-1 min-h-[44px] lg:min-h-0 lg:py-0.5 rounded text-[var(--color-text-muted)] lg:opacity-0 lg:group-hover:opacity-100 hover:bg-white/10 hover:text-red-400"
                  >
                    ✕
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function ToolbarButton({
  onClick,
  busy,
  children,
}: {
  onClick: () => void;
  busy?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      className="px-2 py-1 text-sm rounded border border-white/10 text-[var(--color-text)] hover:bg-white/10 disabled:opacity-50"
    >
      {busy ? '…' : children}
    </button>
  );
}

/**
 * The tappable part of a row — or not, when there is nowhere for it to lead.
 *
 * Three states rather than two: openable, not-yet (an import still running or
 * failed), and nowhere-to-go (a narrow screen, where the desk does not exist).
 * The last one renders no button at all, because a row that looks pressable and
 * is not is the whole list on a phone.
 */
function RowBody({
  onOpen,
  ready,
  children,
}: {
  onOpen?: () => void;
  ready: boolean;
  children: React.ReactNode;
}) {
  const shared = 'flex-1 flex items-center gap-3 text-left min-w-0';
  if (!onOpen) return <div className={shared}>{children}</div>;
  return (
    <button
      type="button"
      onClick={onOpen}
      disabled={!ready}
      className={`${shared} disabled:cursor-default`}
    >
      {children}
    </button>
  );
}
