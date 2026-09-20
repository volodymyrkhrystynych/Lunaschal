import { FormEvent, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type KnowledgeCatalogEntry } from '../../hooks/api';
import { ErrorBanner } from '../LoadStates';
import { KIND_CHIPS, sizeLabel } from '../../lib/knowledge';

// DevDocs is reachable only as a tag — there is no `devdocs` category, so a
// category picker offering it would return nothing. These two shortcuts are
// the reason the panel exists at all, so they get buttons of their own.
const SHORTCUTS = [
  { label: 'DevDocs', filters: { tag: 'devdocs' } },
  { label: 'Stack Exchange', filters: { category: 'stack_exchange' } },
] as const;

function EntryRow({
  entry,
  installed,
  queued,
}: {
  entry: KnowledgeCatalogEntry;
  installed: boolean;
  queued: boolean;
}) {
  const client = useQueryClient();
  const start = useMutation({
    mutationFn: () => api.knowledge.download(entry.name, entry.uuid),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['knowledge'] });
    },
  });

  const already = installed || queued;
  return (
    <div className="rounded border border-white/10 p-2 bg-[var(--color-surface)]">
      <div className="flex items-start gap-2">
        <div className="min-w-0">
          <div className="text-sm font-medium truncate">{entry.title}</div>
          <div className="text-[11px] text-[var(--color-text-muted)] mt-0.5 flex flex-wrap items-center gap-1">
            <span className="px-1 rounded bg-white/10">
              {KIND_CHIPS[entry.kind] ?? entry.kind}
            </span>
            {entry.approxSize !== null && (
              <span>{sizeLabel(entry.approxSize)}</span>
            )}
            {entry.articleCount !== null && (
              <span>{entry.articleCount.toLocaleString()} articles</span>
            )}
            {entry.language && <span>{entry.language}</span>}
          </div>
        </div>
        <button
          type="button"
          disabled={already || start.isPending}
          onClick={() => start.mutate()}
          aria-label={`Download ${entry.title}`}
          className="ml-auto shrink-0 px-2 py-0.5 rounded text-[11px] border border-white/10 disabled:opacity-40 text-[var(--color-primary)]"
        >
          {installed ? 'Installed' : queued ? 'Queued' : 'Download'}
        </button>
      </div>
      {!entry.ftindex && (
        // Hedged on purpose. The catalogue tags all 231 DevDocs entries
        // `_ftindex:no`, but the archives disagree — two checked directly
        // both report a fulltext index and carry no such tag themselves. The
        // real answer is only knowable once the file is on disk, so this
        // says who is claiming it rather than stating it.
        <div
          className="text-[11px] text-amber-400 mt-1"
          title="Checked again once the file is downloaded; the catalogue's flag is not always accurate."
        >
          catalogue says: no fulltext index (title search only)
        </div>
      )}
      {start.isError && (
        <p className="text-[11px] text-red-400 mt-1">
          {(start.error as Error).message}
        </p>
      )}
    </div>
  );
}

export function CatalogPanel({ onClose }: { onClose: () => void }) {
  const [input, setInput] = useState('');
  const [filters, setFilters] = useState<Record<string, string | undefined>>({
    tag: 'devdocs',
  });

  const config = useQuery({
    queryKey: ['knowledge', 'config'],
    queryFn: api.knowledge.config,
  });
  const facets = useQuery({
    queryKey: ['knowledge', 'facets'],
    queryFn: api.knowledge.facets,
  });
  const page = useQuery({
    queryKey: ['knowledge', 'catalog', filters],
    queryFn: () => api.knowledge.catalog(filters),
  });
  const archives = useQuery({
    queryKey: ['knowledge', 'archives'],
    queryFn: api.knowledge.archives,
  });
  const downloads = useQuery({
    queryKey: ['knowledge', 'downloads'],
    queryFn: api.knowledge.downloads,
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    // A title search replaces the tag/category shortcut rather than narrowing
    // it: `q` already searches the whole catalogue, and ANDing it with
    // `tag=devdocs` silently hides everything the user was looking for.
    setFilters({ q: input.trim(), lang: filters.lang });
  };

  const installedNames = new Set(
    (archives.data ?? []).map(archive => archive.filename)
  );
  const queuedNames = new Set(
    (downloads.data ?? [])
      .filter(item => item.status !== 'error')
      .map(item => item.filename)
  );

  const blocked =
    config.data?.writeState && config.data.writeState !== 'writable'
      ? config.data.writeReason
      : null;

  return (
    <div className="space-y-2">
      <div className="flex items-center">
        <h2 className="text-sm font-semibold">Add archives</h2>
        <button
          type="button"
          onClick={onClose}
          className="ml-auto px-2 py-0.5 rounded text-[11px] border border-white/10 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        >
          Done
        </button>
      </div>

      {blocked && (
        <p className="text-xs text-amber-400" role="alert">
          {blocked}
        </p>
      )}

      <form onSubmit={submit} className="flex gap-1">
        <input
          value={input}
          onChange={event => setInput(event.target.value)}
          // The catalogue matches title words, not slugs: `stackoverflow`
          // finds nothing where `Stack Overflow` finds four.
          placeholder="Search by title, e.g. Stack Overflow"
          aria-label="Search the Kiwix catalogue"
          className="flex-1 min-w-0 bg-[var(--color-surface)] border border-white/10 rounded px-2 py-1 text-sm outline-none focus:border-[var(--color-primary)]"
        />
        <button className="px-2 py-1 rounded text-xs bg-[var(--color-primary)]/20 text-[var(--color-primary)]">
          Search
        </button>
      </form>

      <div className="flex flex-wrap gap-1">
        {SHORTCUTS.map(shortcut => (
          <button
            key={shortcut.label}
            type="button"
            onClick={() => {
              setInput('');
              setFilters({ ...shortcut.filters, lang: filters.lang });
            }}
            className="px-2 py-0.5 rounded text-[11px] border border-white/10 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
          >
            {shortcut.label}
          </button>
        ))}
        <select
          aria-label="Language"
          value={filters.lang ?? ''}
          onChange={event =>
            setFilters(current => ({
              ...current,
              lang: event.target.value || undefined,
            }))
          }
          className="ml-auto bg-[var(--color-surface)] border border-white/10 rounded px-1 py-0.5 text-[11px]"
        >
          <option value="">Any language</option>
          {(facets.data?.languages ?? []).map(language => (
            <option key={language.code} value={language.code}>
              {language.label}
              {language.count ? ` (${language.count})` : ''}
            </option>
          ))}
        </select>
      </div>

      {page.isError && <ErrorBanner error={page.error} />}
      {page.isLoading && (
        <p className="text-xs text-[var(--color-text-muted)]">
          Asking the Kiwix catalogue…
        </p>
      )}
      {page.data && (
        <p className="text-[11px] text-[var(--color-text-muted)]">
          {page.data.entries.length} of {page.data.total}
        </p>
      )}

      <div className="space-y-2">
        {(page.data?.entries ?? []).map(entry => (
          <EntryRow
            key={entry.uuid}
            entry={entry}
            installed={[...installedNames].some(name =>
              name.startsWith(entry.name)
            )}
            queued={[...queuedNames].some(name => name.startsWith(entry.name))}
          />
        ))}
      </div>
    </div>
  );
}
