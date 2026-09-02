import { FormEvent, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { formatBytes } from '../../lib/journalAttachments';
import type { PianoArchiveItem } from '../../lib/piano';

const PAGE_SIZE = 100;

export function PianoArchive({
  onLibraryChanged,
}: {
  onLibraryChanged: () => Promise<void>;
}) {
  const queryClient = useQueryClient();
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [offset, setOffset] = useState(0);
  const [message, setMessage] = useState<string | null>(null);

  const status = useQuery({
    queryKey: ['piano', 'archive', 'status'],
    queryFn: api.piano.archiveStatus,
  });
  const listing = useQuery({
    queryKey: ['piano', 'archive', 'items', search, favoritesOnly, offset],
    queryFn: () =>
      api.piano.archiveItems({
        query: search,
        favoritesOnly,
        limit: PAGE_SIZE,
        offset,
      }),
  });

  const refreshArchive = async () => {
    await queryClient.invalidateQueries({ queryKey: ['piano', 'archive'] });
  };

  const scan = useMutation({
    mutationFn: api.piano.archiveScan,
    onSuccess: async result => {
      setMessage(
        `Scan complete: ${result.indexed} new, ${result.updated} updated.`
      );
      setOffset(0);
      await refreshArchive();
    },
  });

  const upload = useMutation({
    mutationFn: async (files: File[]) => {
      for (const file of files) await api.piano.archiveUpload(file);
      return files.length;
    },
    onSuccess: async count => {
      setMessage(`Archived ${count} ${count === 1 ? 'file' : 'files'}.`);
      setOffset(0);
      await refreshArchive();
    },
  });

  const favorite = useMutation({
    mutationFn: ({ item, value }: { item: PianoArchiveItem; value: boolean }) =>
      api.piano.archiveFavorite(item.id, value),
    onSuccess: async item => {
      setMessage(
        item.favorite
          ? item.pianoPieceId
            ? `Added “${item.title}” to the practice library.`
            : `Saved “${item.title}” as an archive favorite.`
          : `Removed “${item.title}” from favorites.`
      );
      await Promise.all([refreshArchive(), onLibraryChanged()]);
    },
  });

  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    setOffset(0);
    setSearch(searchInput.trim());
  };

  const data = status.data;
  const page = listing.data;
  const error =
    mutationError(scan.error) ||
    mutationError(upload.error) ||
    mutationError(favorite.error) ||
    mutationError(status.error) ||
    mutationError(listing.error);

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-2xl font-semibold">Piano Archive</h2>
          {data && (
            <span
              className={`rounded-full px-2 py-0.5 text-xs ${
                data.available
                  ? 'bg-emerald-500/15 text-emerald-300'
                  : 'bg-amber-500/15 text-amber-300'
              }`}
            >
              {data.available
                ? data.writable
                  ? 'Drive connected'
                  : 'Drive read-only'
                : 'Drive unavailable'}
            </span>
          )}
        </div>
        <p className="text-sm text-[var(--color-text-muted)]">
          Bulk files stay on the backup drive. Favorites are cataloged in the
          database; compatible MusicXML favorites also join your local practice
          library.
        </p>
      </header>

      {data && (
        <section className="rounded-lg border border-white/10 bg-[var(--color-surface)] p-4">
          <dl className="grid gap-3 text-sm sm:grid-cols-4">
            <Stat label="Archived" value={String(data.itemCount)} />
            <Stat label="Favorites" value={String(data.favoriteCount)} />
            <Stat label="Catalog size" value={formatBytes(data.sizeBytes)} />
            <Stat
              label="Drive free"
              value={formatBytes(data.freeBytes) || '—'}
            />
          </dl>
          <p className="mt-3 break-all font-mono text-xs text-[var(--color-text-muted)]">
            {data.root ?? data.reason ?? 'Archive is not configured.'}
          </p>
          {!data.writable && data.reason && (
            <p className="mt-2 text-sm text-amber-300">{data.reason}</p>
          )}
        </section>
      )}

      <section className="flex flex-wrap items-center gap-3 rounded-lg border border-white/10 p-4">
        <label
          className={`rounded px-4 py-2 text-sm ${
            data?.writable
              ? 'cursor-pointer bg-[var(--color-primary)] text-white'
              : 'cursor-not-allowed bg-white/10 text-[var(--color-text-muted)]'
          }`}
        >
          {upload.isPending ? 'Uploading…' : 'Add files'}
          <input
            type="file"
            multiple
            disabled={!data?.writable || upload.isPending}
            className="hidden"
            onChange={event => {
              const files = Array.from(event.target.files ?? []);
              if (files.length) upload.mutate(files);
              event.target.value = '';
            }}
          />
        </label>
        <button
          type="button"
          onClick={() => scan.mutate()}
          disabled={!data?.writable || scan.isPending}
          className="rounded border border-white/15 px-4 py-2 text-sm hover:border-white/30 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {scan.isPending ? 'Scanning…' : 'Scan archive folder'}
        </button>
        <span className="text-xs text-[var(--color-text-muted)]">
          Files copied directly into this folder appear after a scan.
        </span>
      </section>

      <form onSubmit={submitSearch} className="flex flex-wrap gap-2">
        <input
          value={searchInput}
          onChange={event => setSearchInput(event.target.value)}
          placeholder="Search title, composer, or filename"
          className="min-w-64 flex-1 rounded border border-white/15 bg-black/20 px-3 py-2 text-sm outline-none focus:border-[var(--color-primary)]"
        />
        <button
          type="submit"
          className="rounded border border-white/15 px-4 py-2 text-sm hover:border-white/30"
        >
          Search
        </button>
        <label className="flex items-center gap-2 rounded border border-white/10 px-3 py-2 text-sm">
          <input
            type="checkbox"
            checked={favoritesOnly}
            onChange={event => {
              setFavoritesOnly(event.target.checked);
              setOffset(0);
            }}
          />
          Favorites only
        </label>
      </form>

      {(message || error) && (
        <p
          role={error ? 'alert' : 'status'}
          className={`rounded border p-3 text-sm ${
            error
              ? 'border-red-500/40 bg-red-500/10 text-red-300'
              : 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
          }`}
        >
          {error ?? message}
        </p>
      )}

      <section className="overflow-hidden rounded-lg border border-white/10">
        <div className="grid grid-cols-[2.5rem_minmax(0,1fr)_auto_auto] gap-3 border-b border-white/10 bg-[var(--color-surface)] px-4 py-2 text-xs uppercase tracking-wide text-[var(--color-text-muted)]">
          <span />
          <span>File</span>
          <span>Type</span>
          <span>Size</span>
        </div>
        {listing.isLoading && (
          <p className="p-6 text-sm text-[var(--color-text-muted)]">
            Loading archive…
          </p>
        )}
        {page?.items.map(item => (
          <ArchiveRow
            key={item.id}
            item={item}
            pending={favorite.isPending}
            onFavorite={value => favorite.mutate({ item, value })}
          />
        ))}
        {page && page.items.length === 0 && (
          <p className="p-8 text-center text-sm text-[var(--color-text-muted)]">
            No archived files match this view.
          </p>
        )}
      </section>

      {page && page.total > PAGE_SIZE && (
        <div className="flex items-center justify-between text-sm">
          <span className="text-[var(--color-text-muted)]">
            {offset + 1}–{Math.min(offset + page.items.length, page.total)} of{' '}
            {page.total}
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={offset === 0}
              onClick={() => setOffset(value => Math.max(0, value - PAGE_SIZE))}
              className="rounded border border-white/15 px-3 py-1.5 disabled:opacity-40"
            >
              Previous
            </button>
            <button
              type="button"
              disabled={offset + page.items.length >= page.total}
              onClick={() => setOffset(value => value + PAGE_SIZE)}
              className="rounded border border-white/15 px-3 py-1.5 disabled:opacity-40"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function ArchiveRow({
  item,
  pending,
  onFavorite,
}: {
  item: PianoArchiveItem;
  pending: boolean;
  onFavorite: (favorite: boolean) => void;
}) {
  const isFavorite = Boolean(item.favorite);
  return (
    <div className="grid grid-cols-[2.5rem_minmax(0,1fr)_auto_auto] items-center gap-3 border-b border-white/5 px-4 py-3 last:border-0">
      <button
        type="button"
        disabled={pending || !item.available}
        aria-label={`${isFavorite ? 'Unfavorite' : 'Favorite'} ${item.title}`}
        title={
          item.practiceCompatible
            ? 'Favorite and add to the practice library'
            : 'Favorite in the archive catalog'
        }
        onClick={() => onFavorite(!isFavorite)}
        className={`text-xl disabled:opacity-40 ${
          isFavorite ? 'text-amber-300' : 'text-[var(--color-text-muted)]'
        }`}
      >
        {isFavorite ? '★' : '☆'}
      </button>
      <div className="min-w-0">
        <a
          href={item.fileUrl}
          className="block truncate font-medium hover:text-[var(--color-primary)]"
          title={item.sourceFilename}
        >
          {item.title}
        </a>
        <p className="truncate text-xs text-[var(--color-text-muted)]">
          {item.creator || item.sourceFilename}
          {item.practiceCompatible ? ' · playable when favorited' : ''}
          {!item.available ? ' · file missing' : ''}
        </p>
      </div>
      <span className="rounded bg-white/5 px-2 py-1 text-xs text-[var(--color-text-muted)]">
        {item.mediaType}
      </span>
      <span className="min-w-16 text-right text-xs text-[var(--color-text-muted)]">
        {formatBytes(item.sizeBytes)}
      </span>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-[var(--color-text-muted)]">{label}</dt>
      <dd className="mt-0.5 font-medium">{value}</dd>
    </div>
  );
}

function mutationError(error: unknown): string | null {
  return error instanceof Error ? error.message : null;
}
