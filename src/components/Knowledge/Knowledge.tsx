import { FormEvent, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type KnowledgeSearchResult } from '../../hooks/api';
import { LoadingState, ErrorBanner } from '../LoadStates';
import { KIND_CHIPS, searchCoverage } from '../../lib/knowledge';
import { ArchiveList } from './ArchiveList';
import { CatalogPanel } from './CatalogPanel';
import { DownloadStrip } from './DownloadStrip';

export function Knowledge() {
  const [input, setInput] = useState('');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<KnowledgeSearchResult | null>(null);
  const [adding, setAdding] = useState(false);
  const client = useQueryClient();
  const config = useQuery({
    queryKey: ['knowledge', 'config'],
    queryFn: api.knowledge.config,
  });
  const archives = useQuery({
    queryKey: ['knowledge', 'archives'],
    queryFn: api.knowledge.archives,
    enabled: config.data?.exists === true,
  });
  const results = useQuery({
    queryKey: ['knowledge', 'search', query],
    queryFn: () => api.knowledge.search(query),
    enabled: query.length > 0,
  });
  const rescan = useMutation({
    mutationFn: api.knowledge.rescan,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['knowledge'] });
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const next = input.trim();
    if (next) {
      setSelected(null);
      setQuery(next);
    }
  };

  if (config.isLoading) {
    return <LoadingState variant="panel" />;
  }
  if (!config.data?.exists) {
    return (
      <div className="flex-1 grid place-items-center p-6">
        <div className="max-w-lg text-center">
          <div className="text-4xl mb-3">📚</div>
          <h2 className="text-xl text-[var(--color-text)]">
            No knowledge library configured
          </h2>
          <p className="mt-2 text-sm text-[var(--color-text-muted)]">
            Choose the folder containing your .zim files in Settings → Knowledge
            Library.
          </p>
        </div>
      </div>
    );
  }

  const coverage = results.data ? searchCoverage(results.data) : null;

  return (
    <div className="flex-1 min-h-0 flex flex-col text-[var(--color-text)]">
      <form
        onSubmit={submit}
        className="shrink-0 p-3 border-b border-white/10 flex gap-2"
      >
        <input
          value={input}
          onChange={event => setInput(event.target.value)}
          placeholder="Search your offline library…"
          aria-label="Search offline library"
          className="flex-1 min-w-0 bg-[var(--color-surface)] border border-white/10 rounded px-3 py-2 outline-none focus:border-[var(--color-primary)]"
        />
        <button className="px-4 py-2 rounded bg-[var(--color-primary)]/20 text-[var(--color-primary)]">
          Search
        </button>
      </form>

      <div className="flex-1 min-h-0 flex flex-col md:flex-row">
        <aside className="md:w-80 md:border-r border-white/10 overflow-y-auto p-3 shrink-0 max-h-[42%] md:max-h-none">
          {!query && adding && (
            <CatalogPanel onClose={() => setAdding(false)} />
          )}
          {!query && !adding && (
            <>
              <div className="flex items-center gap-1 mb-2">
                <h2 className="text-sm font-semibold">Archives</h2>
                <button
                  type="button"
                  onClick={() => setAdding(true)}
                  className="ml-auto px-2 py-0.5 rounded text-[11px] border border-white/10 text-[var(--color-primary)]"
                >
                  Add archives
                </button>
                <button
                  type="button"
                  onClick={() => rescan.mutate()}
                  disabled={rescan.isPending}
                  className="px-2 py-0.5 rounded text-[11px] border border-white/10 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                >
                  {rescan.isPending ? 'Scanning…' : 'Rescan'}
                </button>
              </div>
              {archives.isError && <ErrorBanner error={archives.error} />}
              {archives.data && <ArchiveList archives={archives.data} />}
            </>
          )}
          {/* Outside the !query branch on purpose: a download running while
              you read a search result is still worth seeing finish. */}
          <DownloadStrip />
          {query && (
            <>
              <h2 className="text-sm font-semibold mb-2">
                Results for “{query}”
              </h2>
              {results.isLoading && (
                <p className="text-sm text-[var(--color-text-muted)]">
                  Searching…
                </p>
              )}
              {results.isError && <ErrorBanner error={results.error} />}
              {results.data?.results.length === 0 && (
                <p className="text-sm text-[var(--color-text-muted)]">
                  Nothing found.
                </p>
              )}
              {coverage && (
                <p className="text-xs text-amber-400 mb-2">{coverage}</p>
              )}
              <div className="space-y-1">
                {results.data?.results.map(item => (
                  <button
                    key={`${item.archiveId}:${item.path}`}
                    type="button"
                    onClick={() => setSelected(item)}
                    className={`w-full text-left rounded p-2 border ${selected === item ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/10' : 'border-transparent hover:bg-white/5'}`}
                  >
                    <div className="text-sm font-medium">{item.title}</div>
                    <div className="text-xs text-[var(--color-text-muted)] flex items-center gap-1">
                      <span className="px-1 rounded bg-white/10 shrink-0">
                        {KIND_CHIPS[item.archiveKind] ?? item.archiveKind}
                      </span>
                      <span className="truncate">
                        {item.archiveTitle}
                        {item.archiveDate ? ` · ${item.archiveDate}` : ''}
                      </span>
                    </div>
                  </button>
                ))}
              </div>
            </>
          )}
        </aside>

        <section className="flex-1 min-h-0 bg-white">
          {selected ? (
            <iframe
              key={`${selected.archiveId}:${selected.path}`}
              title={selected.title}
              src={api.knowledge.contentUrl(selected.archiveId, selected.path)}
              sandbox=""
              className="w-full h-full border-0"
            />
          ) : (
            <div className="h-full grid place-items-center text-slate-500 p-6 text-center">
              Search and choose an article to read it here.
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
