import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type CanonSource } from '../../hooks/api';

interface Props {
  projectId: string;
}

const POLL_MS = 2000;

function statusLine(source: CanonSource): string {
  if (source.status === 'importing') {
    const p = source.progress;
    if (p?.stage === 'downloading') return 'Downloading the dump…';
    return `Importing… ${p?.pages ?? 0} pages so far`;
  }
  if (source.status === 'error') return source.error || 'Import failed';
  return `${source.pages.toLocaleString()} pages`;
}

/** The source work a fanfic is set in: Fandom wikis imported from their XML
 * dumps (backend/writing/canon_import.py), shared across projects and linked
 * per project. A linked, ready source is what gives this project's
 * discussions `canon_search`/`canon_read` — nothing else in the app can
 * search it. */
export function CanonPanel({ projectId }: Props) {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [url, setUrl] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);

  const { data: sources } = useQuery({
    queryKey: ['writing', 'canon'],
    queryFn: api.writing.listCanon,
    refetchInterval: query =>
      query.state.data?.some(s => s.status === 'importing') ? POLL_MS : false,
  });
  const { data: linked } = useQuery({
    queryKey: ['writing', 'canon', 'project', projectId],
    queryFn: () => api.writing.projectCanon(projectId),
  });
  const linkedIds = new Set(linked?.sourceIds ?? []);

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ['writing', 'canon'] });

  const setLinks = useMutation({
    mutationFn: (ids: string[]) => api.writing.setProjectCanon(projectId, ids),
    onSuccess: invalidate,
  });
  const fetchUrl = useMutation({
    mutationFn: (link: string) => api.writing.fetchCanon(link),
    onSuccess: ({ id }) => {
      setUrl('');
      setAdding(false);
      setLinks.mutate([...linkedIds, id]);
    },
  });
  const upload = useMutation({
    mutationFn: (file: File) => api.writing.uploadCanon(file),
    onSuccess: ({ id }) => {
      setAdding(false);
      setLinks.mutate([...linkedIds, id]);
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.writing.deleteCanon(id),
    onSuccess: invalidate,
  });

  const toggle = (id: string) => {
    const next = new Set(linkedIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setLinks.mutate([...next]);
  };

  const error = fetchUrl.error || upload.error || remove.error;

  return (
    <div>
      <div className="flex items-center justify-between px-2 pt-3 pb-1">
        <span className="text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">
          Canon
        </span>
        <button
          onClick={() => setAdding(v => !v)}
          className="text-xs px-1.5 rounded text-[var(--color-text-muted)] hover:text-[var(--color-primary)] hover:bg-white/10 transition-colors"
          title="Import a fandom wiki"
        >
          +
        </button>
      </div>

      {adding && (
        <div className="px-2 pb-2 space-y-1.5">
          <div className="flex gap-1">
            <input
              autoFocus
              value={url}
              onChange={e => setUrl(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && url.trim())
                  fetchUrl.mutate(url.trim());
                if (e.key === 'Escape') setAdding(false);
              }}
              placeholder="https://<wiki>.fandom.com or a dump link"
              aria-label="Wiki or dump URL"
              className="flex-1 min-w-0 px-2 py-1 text-xs rounded bg-white/5 border border-white/20 text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)]"
            />
            <button
              onClick={() => url.trim() && fetchUrl.mutate(url.trim())}
              disabled={!url.trim() || fetchUrl.isPending}
              className="px-2 py-1 text-xs rounded bg-[var(--color-primary)]/20 text-[var(--color-primary)] hover:bg-[var(--color-primary)]/30 disabled:opacity-50"
            >
              Import
            </button>
          </div>
          <button
            onClick={() => fileRef.current?.click()}
            disabled={upload.isPending}
            className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] underline disabled:opacity-50"
          >
            {upload.isPending
              ? 'Uploading…'
              : 'or upload a dump file (.7z, .gz, .xml)'}
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".7z,.gz,.bz2,.xml"
            className="hidden"
            onChange={e => {
              const file = e.target.files?.[0];
              if (file) upload.mutate(file);
              e.target.value = '';
            }}
          />
          <p className="text-[11px] text-[var(--color-text-muted)]">
            A wiki link finds the dump on its Special:Statistics page. Fandom
            content is CC BY-SA.
          </p>
        </div>
      )}

      {error && (
        <div className="px-2 pb-1 text-xs text-red-400">
          {error instanceof Error ? error.message : 'Something went wrong'}
        </div>
      )}

      {sources?.map(source => (
        <div
          key={source.id}
          className="group flex items-start gap-2 px-2 py-1.5 rounded hover:bg-white/10"
        >
          <input
            type="checkbox"
            id={`canon-${source.id}`}
            checked={linkedIds.has(source.id)}
            onChange={() => toggle(source.id)}
            className="mt-0.5 accent-[var(--color-primary)]"
          />
          <label
            htmlFor={`canon-${source.id}`}
            className="flex-1 min-w-0 cursor-pointer"
          >
            <span className="block text-sm text-[var(--color-text)] truncate">
              {source.name}
            </span>
            <span
              className={`block text-xs truncate ${
                source.status === 'error'
                  ? 'text-red-400'
                  : 'text-[var(--color-text-muted)]'
              }`}
            >
              {statusLine(source)}
            </span>
          </label>
          {source.status !== 'importing' && (
            <button
              onClick={() => {
                if (confirm(`Delete "${source.name}" for every project?`))
                  remove.mutate(source.id);
              }}
              className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-white/20 text-[var(--color-text-muted)] hover:text-red-400 transition-all shrink-0"
              title="Delete"
            >
              ✕
            </button>
          )}
        </div>
      ))}
      {sources && sources.length === 0 && !adding && (
        <div className="text-sm text-[var(--color-text-muted)] px-2 py-1">
          No canon yet — for fanfic, import the fandom’s wiki
        </div>
      )}
    </div>
  );
}
