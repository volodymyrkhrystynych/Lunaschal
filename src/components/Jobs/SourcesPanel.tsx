import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type JobSearch, type JobSourceKind } from '@/hooks/api';
import {
  describeSearch,
  searchState,
  SOURCE_LABELS,
  sourceNeedsSlug,
} from '@/lib/jobs';

const KINDS: JobSourceKind[] = ['greenhouse', 'lever', 'ashby', 'adzuna'];

/**
 * Where the feed's postings come from. Collapsed by default and parked above
 * the feed rather than buried in Settings — it is configuration you touch
 * while looking at what it produced, not once a year.
 */
export function SourcesPanel() {
  const [open, setOpen] = useState(false);
  const queryClient = useQueryClient();

  const { data: searches } = useQuery({
    queryKey: ['jobs', 'searches'],
    queryFn: api.jobs.searches.list,
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['jobs'] });
  };

  const syncAll = useMutation({
    mutationFn: api.jobs.sync,
    onSuccess: invalidate,
  });

  const count = searches?.length ?? 0;
  const broken = (searches ?? []).filter(s => s.lastError).length;

  return (
    <div className="rounded-lg border border-white/10 bg-[var(--color-surface)]">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between p-3 min-h-[44px] text-left"
      >
        <span className="text-sm text-[var(--color-text)]">
          Sources{' '}
          <span className="text-[var(--color-text-muted)]">
            ({count}
            {broken > 0 && `, ${broken} failing`})
          </span>
        </span>
        <span className="text-xs text-[var(--color-text-muted)]">
          {open ? 'Hide' : 'Show'}
        </span>
      </button>

      {open && (
        <div className="px-3 pb-3 space-y-3 border-t border-white/10 pt-3">
          {count === 0 && (
            <p className="text-xs text-[var(--color-text-muted)]">
              Nothing configured yet, so the feed stays empty. Paste a
              company&rsquo;s careers page below to add them.
            </p>
          )}

          {(searches ?? []).map(search => (
            <SearchRow key={search.id} search={search} onChanged={invalidate} />
          ))}

          <AddSearch onAdded={invalidate} />

          {count > 0 && (
            <button
              type="button"
              onClick={() => syncAll.mutate()}
              disabled={syncAll.isPending}
              className="min-h-[36px] px-3 rounded text-xs border border-white/20 bg-white/5 hover:bg-white/10 disabled:opacity-50"
            >
              {syncAll.isPending ? 'Syncing…' : 'Sync all now'}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function SearchRow({
  search,
  onChanged,
}: {
  search: JobSearch;
  onChanged: () => void;
}) {
  const state = searchState(search);
  const run = useMutation({
    mutationFn: () => api.jobs.searches.run(search.id),
    onSuccess: onChanged,
  });
  const toggle = useMutation({
    mutationFn: () =>
      api.jobs.searches.update(search.id, { enabled: !search.enabled }),
    onSuccess: onChanged,
  });
  const remove = useMutation({
    mutationFn: () => api.jobs.searches.remove(search.id),
    onSuccess: onChanged,
  });

  return (
    <div className="flex items-start gap-2 text-xs">
      <div className="flex-1 min-w-0">
        <p
          className={`truncate ${
            search.enabled
              ? 'text-[var(--color-text)]'
              : 'text-[var(--color-text-muted)] line-through'
          }`}
        >
          {describeSearch(search)}{' '}
          <span className="text-[var(--color-text-muted)]">
            · {SOURCE_LABELS[search.kind]}
          </span>
        </p>
        <p
          className={
            state.tone === 'error'
              ? 'text-red-400 break-words'
              : 'text-[var(--color-text-muted)]'
          }
        >
          {state.text}
        </p>
      </div>
      <button
        type="button"
        onClick={() => run.mutate()}
        disabled={run.isPending}
        className="min-h-[36px] px-2 rounded border border-white/20 bg-white/5 hover:bg-white/10 disabled:opacity-50"
      >
        {run.isPending ? '…' : 'Run'}
      </button>
      <button
        type="button"
        onClick={() => toggle.mutate()}
        className="min-h-[36px] px-2 rounded border border-white/20 bg-white/5 hover:bg-white/10"
      >
        {search.enabled ? 'Pause' : 'Enable'}
      </button>
      <button
        type="button"
        onClick={() => remove.mutate()}
        className="min-h-[36px] px-2 rounded border border-white/20 text-red-400 hover:bg-white/10"
      >
        ×
      </button>
    </div>
  );
}

/**
 * Add a company by pasting its careers page.
 *
 * Not a slug field: slugs cannot be guessed — Ada's Greenhouse board is
 * `ada18` — so asking for one is asking the user to go and find it, which is
 * the tedious half of the job. The backend reads it off the page and checks it
 * against the live board before this offers to add anything.
 */
function AddSearch({ onAdded }: { onAdded: () => void }) {
  const [url, setUrl] = useState('');
  const [manual, setManual] = useState(false);

  const resolve = useMutation({
    mutationFn: () => api.jobs.searches.resolve(url.trim()),
  });

  const add = useMutation({
    mutationFn: (found: {
      kind: JobSourceKind;
      slug: string;
      company: string;
    }) =>
      api.jobs.searches.create({
        kind: found.kind,
        label: found.company,
        params: { slug: found.slug },
      }),
    onSuccess: () => {
      setUrl('');
      resolve.reset();
      onAdded();
    },
  });

  const found = resolve.data;

  return (
    <div className="space-y-2 border-t border-white/10 pt-3">
      <p className="text-xs text-[var(--color-text-muted)]">
        Paste a company&rsquo;s careers page and it will work out which job
        board they use.
      </p>
      <input
        value={url}
        onChange={e => setUrl(e.target.value)}
        onKeyDown={e => {
          if (e.key === 'Enter' && url.trim()) resolve.mutate();
        }}
        placeholder="cohere.com/careers"
        className="w-full min-h-[44px] p-2 rounded bg-[var(--color-bg)] border border-white/10 text-sm text-[var(--color-text)]"
      />
      <button
        type="button"
        onClick={() => resolve.mutate()}
        disabled={!url.trim() || resolve.isPending}
        className="min-h-[36px] px-3 rounded text-xs border border-white/20 bg-white/5 hover:bg-white/10 disabled:opacity-50"
      >
        {resolve.isPending ? 'Checking…' : 'Find their board'}
      </button>

      {resolve.isError && (
        <p className="text-xs text-red-400">
          {(resolve.error as Error).message}
        </p>
      )}

      {found?.kind && (
        <div className="rounded border border-emerald-500/30 bg-emerald-500/5 p-2 space-y-2">
          <p className="text-xs text-[var(--color-text)]">
            {found.company} · {SOURCE_LABELS[found.kind]} ·{' '}
            <span className="text-[var(--color-text-muted)]">
              {found.jobCount} open {found.jobCount === 1 ? 'job' : 'jobs'}
            </span>
          </p>
          <button
            type="button"
            onClick={() =>
              add.mutate({
                kind: found.kind as JobSourceKind,
                slug: found.slug,
                company: found.company,
              })
            }
            disabled={add.isPending}
            className="min-h-[36px] px-3 rounded text-xs bg-[var(--color-primary)]/20 text-[var(--color-primary)] border border-[var(--color-primary)]/40 disabled:opacity-50"
          >
            {add.isPending ? 'Adding…' : 'Add source'}
          </button>
        </div>
      )}

      {found && !found.kind && (
        <div className="rounded border border-white/10 bg-white/5 p-2">
          <p className="text-xs text-[var(--color-text-muted)] break-words">
            {found.error}
          </p>
          {found.detected && (
            <a
              href={found.url || url}
              target="_blank"
              rel="noreferrer"
              className="text-xs text-[var(--color-primary)] min-h-[36px] inline-block"
            >
              Open their careers page ↗
            </a>
          )}
        </div>
      )}

      <button
        type="button"
        onClick={() => setManual(!manual)}
        className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] min-h-[36px]"
      >
        {manual ? 'Hide' : 'Add a board or search by hand'}
      </button>
      {manual && <ManualSearch onAdded={onAdded} />}
    </div>
  );
}

/** The escape hatch: a slug you already know, or an Adzuna query, which has no
 * careers page to resolve. */
function ManualSearch({ onAdded }: { onAdded: () => void }) {
  const [kind, setKind] = useState<JobSourceKind>('greenhouse');
  const [slug, setSlug] = useState('');
  const [what, setWhat] = useState('');
  const [where, setWhere] = useState('');

  const create = useMutation({
    mutationFn: () =>
      api.jobs.searches.create({
        kind,
        params: sourceNeedsSlug(kind) ? { slug: slug.trim() } : { what, where },
      }),
    onSuccess: () => {
      setSlug('');
      setWhat('');
      setWhere('');
      onAdded();
    },
  });

  const ready = sourceNeedsSlug(kind)
    ? slug.trim().length > 0
    : what.trim().length > 0;

  return (
    <div className="space-y-2 pl-2 border-l border-white/10">
      <div className="flex gap-2 flex-wrap">
        {KINDS.map(option => (
          <button
            key={option}
            type="button"
            onClick={() => setKind(option)}
            className={`min-h-[36px] px-3 rounded text-xs border ${
              kind === option
                ? 'border-[var(--color-primary)]/40 bg-[var(--color-primary)]/20 text-[var(--color-primary)]'
                : 'border-white/20 bg-white/5 text-[var(--color-text-muted)]'
            }`}
          >
            {SOURCE_LABELS[option]}
          </button>
        ))}
      </div>

      {sourceNeedsSlug(kind) ? (
        <input
          value={slug}
          onChange={e => setSlug(e.target.value)}
          placeholder="Board slug, e.g. ada18"
          className="w-full min-h-[44px] p-2 rounded bg-[var(--color-bg)] border border-white/10 text-sm text-[var(--color-text)]"
        />
      ) : (
        <>
          <input
            value={what}
            onChange={e => setWhat(e.target.value)}
            placeholder="What, e.g. backend engineer"
            className="w-full min-h-[44px] p-2 rounded bg-[var(--color-bg)] border border-white/10 text-sm text-[var(--color-text)]"
          />
          <input
            value={where}
            onChange={e => setWhere(e.target.value)}
            placeholder="Where, e.g. Toronto"
            className="w-full min-h-[44px] p-2 rounded bg-[var(--color-bg)] border border-white/10 text-sm text-[var(--color-text)]"
          />
          <p className="text-xs text-[var(--color-text-muted)]">
            Adzuna needs an app ID and key in Settings before it returns
            anything.
          </p>
        </>
      )}

      {create.isError && (
        <p className="text-xs text-red-400">
          {(create.error as Error).message}
        </p>
      )}

      <button
        type="button"
        onClick={() => create.mutate()}
        disabled={!ready || create.isPending}
        className="min-h-[36px] px-3 rounded text-xs border border-white/20 bg-white/5 hover:bg-white/10 disabled:opacity-50"
      >
        {create.isPending ? 'Adding…' : 'Add source'}
      </button>
    </div>
  );
}
