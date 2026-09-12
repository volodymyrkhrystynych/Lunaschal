import { FormEvent, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, type KnowledgeSearchResult } from '../../hooks/api';
import { LoadingState, ErrorBanner } from '../LoadStates';

function sizeLabel(bytes: number): string {
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(unit < 2 ? 0 : 1)} ${units[unit]}`;
}

export function Knowledge() {
  const [input, setInput] = useState('');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<KnowledgeSearchResult | null>(null);
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
          {!query && (
            <>
              <h2 className="text-sm font-semibold mb-2">Archives</h2>
              {archives.isError && <ErrorBanner error={archives.error} />}
              {archives.data?.length === 0 && (
                <p className="text-sm text-[var(--color-text-muted)]">
                  No .zim files found in this folder.
                </p>
              )}
              <div className="space-y-2">
                {archives.data?.map(item => (
                  <div
                    key={item.id}
                    className="rounded border border-white/10 p-3 bg-[var(--color-surface)]"
                  >
                    <div className="font-medium text-sm">{item.title}</div>
                    <div className="text-xs text-[var(--color-text-muted)] mt-1">
                      {[item.date, item.language, sizeLabel(item.size)]
                        .filter(Boolean)
                        .join(' · ')}
                    </div>
                    {item.error && (
                      <div className="text-xs text-red-400 mt-1">
                        {item.error}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}
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
              {results.data?.length === 0 && (
                <p className="text-sm text-[var(--color-text-muted)]">
                  Nothing found.
                </p>
              )}
              <div className="space-y-1">
                {results.data?.map(item => (
                  <button
                    key={`${item.archiveId}:${item.path}`}
                    type="button"
                    onClick={() => setSelected(item)}
                    className={`w-full text-left rounded p-2 border ${selected === item ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/10' : 'border-transparent hover:bg-white/5'}`}
                  >
                    <div className="text-sm font-medium">{item.title}</div>
                    <div className="text-xs text-[var(--color-text-muted)]">
                      {item.archiveTitle}
                      {item.archiveDate ? ` · ${item.archiveDate}` : ''}
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
