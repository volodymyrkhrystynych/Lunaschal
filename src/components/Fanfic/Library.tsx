import { useEffect, useRef, useState } from 'react';
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { api } from '../../hooks/api';
import type { Fic, RefreshAlertsResult } from '../../hooks/api';
import {
  detectFicSite,
  formatRating,
  siteLabel,
  SITE_LABELS,
} from '../../lib/fanfic';
import { useShortcutScope } from '../../shortcuts/ShortcutProvider';
import { useListSelection } from '../../shortcuts/useListSelection';
import { FolderBar, FolderPicker } from './Folders';

interface LibraryProps {
  onOpen: (ficId: string) => void;
}

const formatWords = (n: number) =>
  n >= 1000 ? `${Math.round(n / 1000)}k words` : `${n} words`;

const formatDate = (date: string) =>
  new Intl.DateTimeFormat('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  }).format(new Date(date));

const PAGE_SIZE = 50;

export function Library({ onOpen }: LibraryProps) {
  const [view, setView] = useState<'library' | 'folders'>('library');
  const [searchQuery, setSearchQuery] = useState('');
  const [showImport, setShowImport] = useState(false);
  const [importMode, setImportMode] = useState<'forum' | 'file'>('forum');
  const [importUrl, setImportUrl] = useState('');
  const [importError, setImportError] = useState<string | null>(null);
  const [folderId, setFolderId] = useState<string | null>(null);
  const [tag, setTag] = useState<string | null>(null);
  const [showDelete, setShowDelete] = useState(false);
  const [refreshSummary, setRefreshSummary] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const sentinelRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();

  const listQuery = useInfiniteQuery({
    queryKey: ['fanfic', 'list', folderId, tag],
    queryFn: ({ pageParam }) =>
      api.fanfic.list({
        folderId: folderId && folderId !== 'recent' ? folderId : undefined,
        tag: tag ?? undefined,
        sort: folderId === 'recent' ? 'recent' : undefined,
        limit: PAGE_SIZE,
        offset: pageParam,
      }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, allPages) =>
      lastPage.length === PAGE_SIZE ? allPages.length * PAGE_SIZE : undefined,
    enabled: !searchQuery && (view === 'library' || !!folderId),
    // Poll while any fic is still downloading or queued behind the serial
    // update worker so progress bars and queued badges advance.
    refetchInterval: query =>
      query.state.data?.pages.some(page =>
        page.some(
          (f: Fic) => f.downloadStatus === 'downloading' || f.updatePending
        )
      )
        ? 1500
        : false,
  });

  const searchResults = useQuery({
    queryKey: ['fanfic', 'search', searchQuery],
    queryFn: () => api.fanfic.search(searchQuery),
    enabled: !!searchQuery,
  });

  const fics = searchQuery
    ? searchResults.data
    : view === 'folders' && !folderId
      ? []
      : listQuery.data?.pages.flat();
  const isLoading = searchQuery ? searchResults.isLoading : listQuery.isLoading;

  // Fetch the next page once the sentinel at the bottom of the scrollable
  // list comes near view, instead of the library ever loading it all at once.
  useEffect(() => {
    if (searchQuery) return;
    if (typeof IntersectionObserver === 'undefined') return;
    const sentinel = sentinelRef.current;
    const root = listRef.current;
    if (!sentinel || !root) return;
    const observer = new IntersectionObserver(
      entries => {
        if (
          entries[0].isIntersecting &&
          listQuery.hasNextPage &&
          !listQuery.isFetchingNextPage
        ) {
          listQuery.fetchNextPage();
        }
      },
      { root, rootMargin: '200px' }
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [
    searchQuery,
    listQuery.hasNextPage,
    listQuery.isFetchingNextPage,
    listQuery.fetchNextPage,
  ]);

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ['fanfic'] });

  const importFic = useMutation({
    mutationFn: (url: string) => api.fanfic.importUrl(url),
    onSuccess: result => {
      invalidate();
      setImportUrl('');
      setShowImport(false);
      setSearchQuery('');
      if (result.alreadyExists) setImportError(null);
    },
    onError: (e: Error) => setImportError(e.message),
  });

  const uploadFile = useMutation({
    mutationFn: (file: File) => api.fanfic.uploadFile(file),
    onSuccess: () => {
      invalidate();
      setShowImport(false);
      setImportError(null);
    },
    onError: (e: Error) => setImportError(e.message),
  });

  const checkUpdates = useMutation({
    mutationFn: ({ ficId, deep }: { ficId: string; deep?: boolean }) =>
      api.fanfic.checkUpdates(ficId, deep),
    onSuccess: invalidate,
    onError: (e: Error) => setImportError(e.message),
  });

  const deleteFic = useMutation({
    mutationFn: (ficId: string) => api.fanfic.delete(ficId),
    onSuccess: invalidate,
  });

  const refreshAlerts = useMutation({
    mutationFn: () => api.fanfic.refreshAlerts(),
    onSuccess: (r: RefreshAlertsResult) => {
      invalidate();
      const parts = [];
      if (r.flagged) parts.push(`${r.flagged} queued for update`);
      if (r.newImports)
        parts.push(`${r.newImports} new import${r.newImports > 1 ? 's' : ''}`);
      if (r.skippedActive) parts.push(`${r.skippedActive} already queued`);
      if (parts.length === 0)
        parts.push(`no library threads among ${r.alertsSeen} alerts`);
      const siteErrors = Object.entries(r.errors).map(
        ([d, msg]) => `${d}: ${msg}`
      );
      setRefreshSummary([parts.join(' · '), ...siteErrors].join(' — '));
      setImportError(null);
    },
    onError: (e: Error) => setImportError(e.message),
  });

  const { selIndex, next, prev, isSelected } = useListSelection(
    fics?.length,
    1
  );

  useShortcutScope(1, {
    next,
    prev,
    create: () => setShowImport(true),
    search: () => {
      searchInputRef.current?.focus();
      searchInputRef.current?.select();
    },
    drillIn: () => {
      const fic = fics?.[selIndex];
      if (!fic) return false;
      onOpen(fic.id);
      return true;
    },
  });

  const importSite = detectFicSite(importUrl);

  return (
    <div className="flex-1 flex flex-col p-4 overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-4">
        {/* One button, not a pair: on a phone this row already carries Refresh,
            the delete toggle and Import, and two view buttons pushed it over
            the width. So the switch says where you are and swaps on a tap —
            the glyph is what marks it tappable rather than a label, and the
            title says what the tap will do. */}
        {!searchQuery ? (
          <button
            onClick={() => {
              setView(view === 'library' ? 'folders' : 'library');
              setFolderId(null);
            }}
            title={
              view === 'library'
                ? 'Switch to folders'
                : 'Switch to the whole library'
            }
            className="px-3 py-1.5 text-sm rounded-lg border border-[var(--color-primary)] bg-[var(--color-primary)]/20 text-[var(--color-text)]"
          >
            {view === 'library' ? 'Library' : 'Folders'}
            <span aria-hidden="true" className="ml-1.5 opacity-60">
              ⇄
            </span>
          </button>
        ) : (
          <div />
        )}
        <div className="flex flex-wrap justify-end gap-2">
          <button
            onClick={() => refreshAlerts.mutate()}
            disabled={refreshAlerts.isPending}
            title="Check each site's alerts page and queue updates for threads with new activity"
            className="px-2.5 py-1 text-sm md:px-4 md:py-2 md:text-base border border-white/20 text-[var(--color-text)] rounded-lg hover:bg-white/10 transition-colors disabled:opacity-50"
          >
            {refreshAlerts.isPending ? 'Checking…' : '⟳ Refresh'}
          </button>
          <button
            onClick={() => setShowDelete(!showDelete)}
            title={showDelete ? 'Hide delete buttons' : 'Show delete buttons'}
            className={`px-2.5 py-1 text-sm md:px-4 md:py-2 md:text-base border rounded-lg transition-colors ${
              showDelete
                ? 'border-red-400/50 text-red-400 bg-red-500/10'
                : 'border-white/20 text-[var(--color-text-muted)] hover:bg-white/10'
            }`}
          >
            🗑
          </button>
          <button
            onClick={() => setShowImport(!showImport)}
            className="px-2.5 py-1 text-sm md:px-4 md:py-2 md:text-base bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 transition-colors"
          >
            + Import
          </button>
        </div>
      </div>

      <div className="mb-4">
        <input
          ref={searchInputRef}
          type="text"
          value={searchQuery}
          onChange={e => setSearchQuery(e.target.value)}
          placeholder="Search titles and tags..."
          className="w-full bg-[var(--color-surface)] border border-white/10 rounded-lg px-4 py-2 text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)]"
        />
      </div>

      {!searchQuery && (
        <div>
          <div className="flex flex-wrap items-center gap-2">
            {view === 'library' ? (
              <div className="tag-row flex flex-wrap items-center gap-2 mb-4">
                {[
                  [null, 'All', 'Books ordered by latest chapter publication'],
                  ['recent', 'Recent', 'Books ordered by most recently opened'],
                  ['unsorted', 'Unsorted', 'Books not assigned to a folder'],
                ].map(([id, label, title]) => (
                  <button
                    key={label}
                    title={title ?? undefined}
                    onClick={() => setFolderId(id)}
                    className={`px-3 py-1 text-sm rounded-full border transition-colors ${
                      folderId === id
                        ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/20 text-[var(--color-text)]'
                        : 'border-white/15 text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            ) : (
              <FolderBar
                folderId={folderId}
                onSelect={setFolderId}
                showDefaults={false}
              />
            )}
            {tag && (
              <button
                onClick={() => setTag(null)}
                className="mb-4 px-3 py-1 text-sm rounded-full border border-[var(--color-primary)] bg-[var(--color-primary)]/20 text-[var(--color-text)]"
                title="Clear tag filter"
              >
                tag: {tag} ✕
              </button>
            )}
          </div>
        </div>
      )}

      {showImport && (
        <div className="mb-4 p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
          <div
            className="flex gap-2 mb-3"
            role="tablist"
            aria-label="Import source"
          >
            {(
              [
                ['forum', 'From forum'],
                ['file', 'Upload file'],
              ] as const
            ).map(([mode, label]) => (
              <button
                key={mode}
                role="tab"
                aria-selected={importMode === mode}
                onClick={() => {
                  setImportMode(mode);
                  setImportError(null);
                }}
                className={`px-3 py-1.5 text-sm rounded-lg border ${
                  importMode === mode
                    ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/20 text-[var(--color-text)]'
                    : 'border-white/15 text-[var(--color-text-muted)]'
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          {importMode === 'forum' ? (
            <>
              <div className="text-sm text-[var(--color-text-muted)] mb-2">
                Paste any link to the fic — a chapter, the thread, or the
                reader. The whole fic (all threadmarks, sidestories and images)
                is downloaded for offline reading.
              </div>
              <input
                value={importUrl}
                autoFocus
                onChange={e => {
                  setImportUrl(e.target.value);
                  setImportError(null);
                }}
                onKeyDown={e => {
                  if (e.key === 'Escape') setShowImport(false);
                  if (e.key === 'Enter' && importUrl.trim())
                    importFic.mutate(importUrl.trim());
                }}
                placeholder="https://forums.spacebattles.com/threads/..."
                className="w-full bg-transparent text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none border border-white/10 rounded p-2 mb-2"
              />
              {importSite && (
                <div className="mb-2 text-xs text-[var(--color-primary)]">
                  {SITE_LABELS[importSite]} thread detected
                </div>
              )}
            </>
          ) : (
            <>
              <div className="text-sm text-[var(--color-text-muted)] mb-2">
                Import a fic you already have as a file — EPUB, DOCX or PDF.
                It's split into chapters for the reader.
              </div>
              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={uploadFile.isPending}
                className="mb-2 px-3 py-1.5 text-sm border border-white/20 text-[var(--color-text)] rounded-lg hover:bg-white/10 transition-colors disabled:opacity-50"
              >
                {uploadFile.isPending ? 'Importing…' : 'Choose file…'}
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept=".epub,.docx,.pdf"
                className="hidden"
                onChange={e => {
                  const file = e.target.files?.[0];
                  if (file) uploadFile.mutate(file);
                  e.target.value = '';
                }}
              />
            </>
          )}

          {importError && (
            <div className="mb-2 px-3 py-2 bg-red-500/10 border border-red-500/20 rounded text-sm text-red-400">
              {importError}
            </div>
          )}
          <div className="flex justify-end gap-2">
            <button
              onClick={() => {
                setShowImport(false);
                setImportError(null);
              }}
              className="px-3 py-1 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            >
              Cancel
            </button>
            {importMode === 'forum' && (
              <button
                onClick={() => importFic.mutate(importUrl.trim())}
                disabled={!importUrl.trim() || importFic.isPending}
                className="px-3 py-1 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
              >
                {importFic.isPending ? 'Starting…' : 'Import'}
              </button>
            )}
          </div>
        </div>
      )}

      {!showImport && importError && (
        <div className="mb-4 px-3 py-2 bg-red-500/10 border border-red-500/20 rounded text-sm text-red-400 flex justify-between">
          <span>{importError}</span>
          <button
            onClick={() => setImportError(null)}
            className="ml-2 hover:text-red-300"
          >
            ✕
          </button>
        </div>
      )}

      {refreshSummary && (
        <div className="mb-4 px-3 py-2 bg-[var(--color-surface)] border border-white/15 rounded text-sm text-[var(--color-text-muted)] flex justify-between">
          <span>{refreshSummary}</span>
          <button
            onClick={() => setRefreshSummary(null)}
            className="ml-2 hover:text-[var(--color-text)]"
          >
            ✕
          </button>
        </div>
      )}

      <div
        ref={listRef}
        className="flex-1 overflow-y-auto overflow-x-hidden space-y-3"
      >
        {isLoading && (
          <div className="text-[var(--color-text-muted)]">Loading...</div>
        )}

        {fics?.map((fic, idx) => (
          <FicCard
            key={fic.id}
            fic={fic}
            selected={isSelected(idx)}
            showDelete={showDelete}
            onOpen={() => onOpen(fic.id)}
            onCheckUpdates={deep =>
              checkUpdates.mutate({ ficId: fic.id, deep })
            }
            onTagClick={name => {
              setSearchQuery('');
              setTag(name);
            }}
            onDelete={() => {
              if (window.confirm(`Delete "${fic.title}" and all its chapters?`))
                deleteFic.mutate(fic.id);
            }}
          />
        ))}

        {fics?.length === 0 && !isLoading && (
          <div className="text-center text-[var(--color-text-muted)] py-12">
            {searchQuery
              ? 'No fics match'
              : view === 'folders' && !folderId
                ? 'Choose a folder to browse and group its books.'
                : 'Nothing here yet — import a fic from a forum or upload an EPUB/DOCX/PDF.'}
          </div>
        )}

        {!searchQuery && (
          <div ref={sentinelRef} className="h-1">
            {listQuery.isFetchingNextPage && (
              <div className="text-center text-[var(--color-text-muted)] text-sm py-2">
                Loading more…
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function FicCard({
  fic,
  selected,
  showDelete,
  onOpen,
  onCheckUpdates,
  onTagClick,
  onDelete,
}: {
  fic: Fic;
  selected: boolean;
  showDelete: boolean;
  onOpen: () => void;
  onCheckUpdates: (deep?: boolean) => void;
  onTagClick: (name: string) => void;
  onDelete: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [showReview, setShowReview] = useState(false);
  const downloading = fic.downloadStatus === 'downloading';
  const progress = fic.downloadProgress;
  const pct = progress?.chaptersTotal
    ? Math.min(
        100,
        Math.round((progress.chaptersDone / progress.chaptersTotal) * 100)
      )
    : null;
  const badge =
    fic.sourceType === 'xenforo'
      ? siteLabel(fic.site)
      : fic.sourceType.toUpperCase();

  const toggleDetailsFromCard = (event: React.MouseEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement;
    // Buttons and links keep their own actions. Clicking the title opens the
    // reader; clicking the otherwise-empty card surface toggles its details.
    if (target.closest('button, a, input, textarea, select')) return;
    setExpanded(value => !value);
  };

  return (
    <div
      ref={el => {
        if (el && selected) el.scrollIntoView({ block: 'nearest' });
      }}
      onClick={toggleDetailsFromCard}
      className={`p-4 bg-[var(--color-surface)] rounded-lg border cursor-pointer ${selected ? 'border-[var(--color-primary)]' : 'border-white/10'}`}
    >
      <div className="flex items-start gap-3">
        {fic.coverPath && (
          <img
            src={`/api/fanfic/${fic.id}/images/${fic.coverPath}`}
            alt=""
            className="w-12 h-16 object-cover rounded border border-white/10 shrink-0"
          />
        )}
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <button
              onClick={onOpen}
              className="min-w-0 break-words text-left text-base font-bold text-[var(--color-text)] hover:text-[var(--color-primary)] transition-colors"
            >
              {fic.title}
            </button>
            <div className="flex flex-wrap gap-2 shrink-0">
              <button
                onClick={() => setExpanded(value => !value)}
                aria-expanded={expanded}
                className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                title="Show summary and review"
              >
                {expanded ? '▾ Details' : '▸ Details'}
              </button>
              <FolderPicker fic={fic} />
              <button
                onClick={() => setShowReview(!showReview)}
                className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                title="Rate and review"
              >
                Review
              </button>
              {fic.sourceType === 'xenforo' && !downloading && (
                <>
                  <button
                    onClick={() => onCheckUpdates(false)}
                    className={`text-sm ${
                      fic.updatePending
                        ? 'text-[var(--color-primary)] hover:text-[var(--color-text)]'
                        : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
                    }`}
                    title={
                      fic.updatePending
                        ? 'Waiting for the update worker — click to un-queue'
                        : 'Queue an update check for new chapters'
                    }
                  >
                    {fic.updatePending && !fic.deepPending
                      ? '⏳ Queued'
                      : '↻ Update'}
                  </button>
                  <button
                    onClick={() => onCheckUpdates(true)}
                    className={`text-sm ${
                      fic.deepPending
                        ? 'text-[var(--color-primary)] hover:text-[var(--color-text)]'
                        : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
                    }`}
                    title={
                      fic.deepPending
                        ? 'Deep check queued — click to un-queue'
                        : 'Re-read every saved chapter and pull in any the author has edited. Slower: it refetches the whole fic.'
                    }
                  >
                    {fic.deepPending ? '⏳ Deep' : '↻↻ Deep'}
                  </button>
                </>
              )}
              {showDelete && (
                <button
                  onClick={onDelete}
                  className="text-sm text-red-400 hover:text-red-300"
                >
                  Delete
                </button>
              )}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-sm text-[var(--color-text-muted)] mt-0.5">
            {fic.author && <span>{fic.author}</span>}
            {formatRating(fic.rating) && (
              <span className="text-amber-400" title={`Rated ${fic.rating}/5`}>
                {formatRating(fic.rating)}
              </span>
            )}
            {badge && (
              <span className="px-1.5 py-0.5 text-xs rounded border border-white/20">
                {badge}
              </span>
            )}
            {(fic.folderIds?.length ?? 0) === 0 && (
              <span
                className="px-1.5 py-0.5 text-xs rounded border border-amber-400/40 text-amber-300"
                title="Not sorted into any folder"
              >
                Unsorted
              </span>
            )}
            {fic.chapterCount > 0 && <span>{fic.chapterCount} chapters</span>}
            {(fic.readCount ?? 0) > 0 && fic.chapterCount > 0 && (
              <span title="Chapters read">
                {fic.readCount}/{fic.chapterCount} read
              </span>
            )}
            {fic.wordCount > 0 && <span>{formatWords(fic.wordCount)}</span>}
            <span>added {formatDate(fic.createdAt)}</span>
          </div>

          {fic.tags && fic.tags.length > 0 && (
            <div className="tag-row flex flex-wrap gap-1 mt-1.5">
              {fic.tags.map(name => (
                <button
                  key={name}
                  onClick={() => onTagClick(name)}
                  className="px-1.5 py-0.5 text-xs rounded border border-white/15 text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-white/30 transition-colors"
                  title={`Filter library by "${name}"`}
                >
                  {name}
                </button>
              ))}
            </div>
          )}

          {expanded && (
            <div className="mt-3 space-y-3 border-t border-white/10 pt-3 text-sm">
              <section>
                <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
                  My rating
                </h3>
                <p
                  className={
                    formatRating(fic.rating)
                      ? 'text-amber-400'
                      : 'text-[var(--color-text-muted)]'
                  }
                >
                  {formatRating(fic.rating) || 'Not rated'}
                </p>
              </section>
              <section>
                <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
                  Summary
                </h3>
                <p className="whitespace-pre-wrap text-[var(--color-text)]">
                  {fic.description?.trim() || 'No summary available.'}
                </p>
              </section>
              {fic.review?.trim() && (
                <section>
                  <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
                    Review
                  </h3>
                  <p className="whitespace-pre-wrap text-[var(--color-text)]">
                    {fic.review}
                  </p>
                </section>
              )}
            </div>
          )}

          {showReview && (
            <ReviewEditor fic={fic} onClose={() => setShowReview(false)} />
          )}

          {downloading && (
            <div className="mt-2">
              <div className="flex justify-between text-xs text-[var(--color-text-muted)] mb-1">
                <span>
                  {progress?.phase === 'index'
                    ? 'Fetching index…'
                    : progress?.phase === 'updating'
                      ? 'Checking for new chapters…'
                      : `Downloading chapters… ${progress?.chaptersDone ?? 0}${progress?.chaptersTotal ? ` / ${progress.chaptersTotal}` : ''}`}
                </span>
                {pct !== null && <span>{pct}%</span>}
              </div>
              <div className="h-1.5 bg-white/10 rounded overflow-hidden">
                <div
                  className="h-full bg-[var(--color-primary)] transition-all"
                  style={{
                    width: pct !== null ? `${pct}%` : '100%',
                    opacity: pct !== null ? 1 : 0.4,
                  }}
                />
              </div>
            </div>
          )}

          {fic.downloadStatus === 'error' && fic.downloadError && (
            <div className="mt-2 px-3 py-2 bg-red-500/10 border border-red-500/20 rounded text-sm text-red-400">
              {fic.downloadError}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ReviewEditor({ fic, onClose }: { fic: Fic; onClose: () => void }) {
  const [rating, setRating] = useState<number | null>(fic.rating);
  const [text, setText] = useState(fic.review ?? '');
  const queryClient = useQueryClient();

  const save = useMutation({
    mutationFn: () =>
      api.fanfic.saveReview(fic.id, {
        rating,
        review: text.trim() || null,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['fanfic'] });
      onClose();
    },
  });

  return (
    <div className="mt-2 p-3 bg-black/20 rounded-lg border border-white/10">
      <div className="flex items-center gap-1 mb-2">
        {[1, 2, 3, 4, 5].map(n => (
          <button
            key={n}
            onClick={() => setRating(rating === n ? null : n)}
            className={`text-xl leading-none ${rating !== null && n <= rating ? 'text-amber-400' : 'text-white/25 hover:text-white/50'}`}
            title={rating === n ? 'Clear rating' : `Rate ${n}/5`}
          >
            ★
          </button>
        ))}
        {rating !== null && (
          <span className="ml-1 text-xs text-[var(--color-text-muted)]">
            {rating}/5
          </span>
        )}
      </div>
      <textarea
        value={text}
        onChange={e => setText(e.target.value)}
        placeholder="Your overall thoughts on this fic…"
        rows={3}
        className="w-full bg-transparent text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none border border-white/10 rounded p-2 resize-y"
      />
      <div className="flex justify-end gap-2 mt-1">
        <button
          onClick={onClose}
          className="px-3 py-1 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        >
          Cancel
        </button>
        <button
          onClick={() => save.mutate()}
          disabled={save.isPending}
          className="px-3 py-1 text-sm bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
        >
          {save.isPending ? 'Saving…' : 'Save'}
        </button>
      </div>
    </div>
  );
}
