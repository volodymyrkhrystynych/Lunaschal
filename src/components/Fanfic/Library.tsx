import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import type { Fic } from '../../hooks/api';
import { detectFicSite, formatRating, siteLabel, SITE_LABELS } from '../../lib/fanfic';
import { useShortcuts, useShortcutScope } from '../../shortcuts/ShortcutProvider';
import { FolderBar, FolderPicker } from './Folders';

interface LibraryProps {
  onOpen: (ficId: string) => void;
}

const formatWords = (n: number) => (n >= 1000 ? `${Math.round(n / 1000)}k words` : `${n} words`);

const formatDate = (date: string) =>
  new Intl.DateTimeFormat('en-US', { year: 'numeric', month: 'short', day: 'numeric' }).format(new Date(date));

export function Library({ onOpen }: LibraryProps) {
  const [searchQuery, setSearchQuery] = useState('');
  const [showImport, setShowImport] = useState(false);
  const [importUrl, setImportUrl] = useState('');
  const [importError, setImportError] = useState<string | null>(null);
  const [selIndex, setSelIndex] = useState(0);
  const [folderId, setFolderId] = useState<string | null>(null);
  const [tag, setTag] = useState<string | null>(null);
  const [showDelete, setShowDelete] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();
  const { level } = useShortcuts();

  const { data: fics, isLoading } = useQuery({
    queryKey: searchQuery ? ['fanfic', 'search', searchQuery] : ['fanfic', 'list', folderId, tag],
    queryFn: () => (searchQuery
      ? api.fanfic.search(searchQuery)
      : api.fanfic.list({ folderId: folderId ?? undefined, tag: tag ?? undefined })),
    // Poll while any fic is still downloading so progress bars advance.
    refetchInterval: (query) =>
      query.state.data?.some((f: Fic) => f.downloadStatus === 'downloading') ? 1500 : false,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['fanfic'] });

  const importFic = useMutation({
    mutationFn: (url: string) => api.fanfic.importUrl(url),
    onSuccess: (result) => {
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
      setImportError(null);
    },
    onError: (e: Error) => setImportError(e.message),
  });

  const checkUpdates = useMutation({
    mutationFn: (ficId: string) => api.fanfic.checkUpdates(ficId),
    onSuccess: invalidate,
    onError: (e: Error) => setImportError(e.message),
  });

  const deleteFic = useMutation({
    mutationFn: (ficId: string) => api.fanfic.delete(ficId),
    onSuccess: invalidate,
  });

  useEffect(() => {
    setSelIndex((i) => Math.min(i, Math.max((fics?.length ?? 1) - 1, 0)));
  }, [fics]);

  useShortcutScope(1, {
    next: () => setSelIndex((i) => Math.min(i + 1, Math.max((fics?.length ?? 1) - 1, 0))),
    prev: () => setSelIndex((i) => Math.max(i - 1, 0)),
    create: () => setShowImport(true),
    drillIn: () => {
      const fic = fics?.[selIndex];
      if (!fic) return false;
      onOpen(fic.id);
      return true;
    },
    scrollDown: () => listRef.current?.scrollBy({ top: 120, behavior: 'smooth' }),
    scrollUp: () => listRef.current?.scrollBy({ top: -120, behavior: 'smooth' }),
  });

  const importSite = detectFicSite(importUrl);

  return (
    <div className="flex-1 flex flex-col p-4 overflow-hidden">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-semibold text-[var(--color-text)]">Library</h1>
        <div className="flex gap-2">
          <button onClick={() => fileInputRef.current?.click()}
            disabled={uploadFile.isPending}
            className="px-4 py-2 border border-white/20 text-[var(--color-text)] rounded-lg hover:bg-white/10 transition-colors disabled:opacity-50">
            {uploadFile.isPending ? 'Importing…' : 'Upload file'}
          </button>
          <input ref={fileInputRef} type="file" accept=".epub,.docx,.pdf" className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) uploadFile.mutate(file);
              e.target.value = '';
            }} />
          <button onClick={() => setShowDelete(!showDelete)}
            title={showDelete ? 'Hide delete buttons' : 'Show delete buttons'}
            className={`px-4 py-2 border rounded-lg transition-colors ${
              showDelete
                ? 'border-red-400/50 text-red-400 bg-red-500/10'
                : 'border-white/20 text-[var(--color-text-muted)] hover:bg-white/10'
            }`}>
            🗑
          </button>
          <button onClick={() => setShowImport(!showImport)}
            className="px-4 py-2 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 transition-colors">
            + Import from forum
          </button>
        </div>
      </div>

      <div className="mb-4">
        <input type="text" value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="Search fics and chapter text..."
          className="w-full bg-[var(--color-surface)] border border-white/10 rounded-lg px-4 py-2 text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-primary)]" />
      </div>

      {!searchQuery && (
        <div className="flex flex-wrap items-center gap-2">
          <FolderBar folderId={folderId} onSelect={setFolderId} />
          {tag && (
            <button onClick={() => setTag(null)}
              className="mb-4 px-3 py-1 text-sm rounded-full border border-[var(--color-primary)] bg-[var(--color-primary)]/20 text-[var(--color-text)]"
              title="Clear tag filter">
              tag: {tag} ✕
            </button>
          )}
        </div>
      )}

      {showImport && (
        <div className="mb-4 p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
          <div className="text-sm text-[var(--color-text-muted)] mb-2">
            Paste any link to the fic — a chapter, the thread, or the reader. The whole fic
            (all threadmarks, sidestories and images) is downloaded for offline reading.
          </div>
          <input value={importUrl} autoFocus
            onChange={(e) => { setImportUrl(e.target.value); setImportError(null); }}
            onKeyDown={(e) => {
              if (e.key === 'Escape') setShowImport(false);
              if (e.key === 'Enter' && importUrl.trim()) importFic.mutate(importUrl.trim());
            }}
            placeholder="https://forums.spacebattles.com/threads/..."
            className="w-full bg-transparent text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none border border-white/10 rounded p-2 mb-2" />
          {importSite && (
            <div className="mb-2 text-xs text-[var(--color-primary)]">{SITE_LABELS[importSite]} thread detected</div>
          )}
          {importError && (
            <div className="mb-2 px-3 py-2 bg-red-500/10 border border-red-500/20 rounded text-sm text-red-400">{importError}</div>
          )}
          <div className="flex justify-end gap-2">
            <button onClick={() => { setShowImport(false); setImportError(null); }}
              className="px-3 py-1 text-[var(--color-text-muted)] hover:text-[var(--color-text)]">Cancel</button>
            <button onClick={() => importFic.mutate(importUrl.trim())}
              disabled={!importUrl.trim() || importFic.isPending}
              className="px-3 py-1 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50">
              {importFic.isPending ? 'Starting…' : 'Import'}
            </button>
          </div>
        </div>
      )}

      {!showImport && importError && (
        <div className="mb-4 px-3 py-2 bg-red-500/10 border border-red-500/20 rounded text-sm text-red-400 flex justify-between">
          <span>{importError}</span>
          <button onClick={() => setImportError(null)} className="ml-2 hover:text-red-300">✕</button>
        </div>
      )}

      <div ref={listRef} className="flex-1 overflow-y-auto space-y-3">
        {isLoading && <div className="text-[var(--color-text-muted)]">Loading...</div>}

        {fics?.map((fic, idx) => (
          <FicCard key={fic.id} fic={fic}
            selected={level >= 1 && idx === selIndex}
            showDelete={showDelete}
            onOpen={() => onOpen(fic.id)}
            onCheckUpdates={() => checkUpdates.mutate(fic.id)}
            onTagClick={(name) => { setSearchQuery(''); setTag(name); }}
            onDelete={() => {
              if (window.confirm(`Delete "${fic.title}" and all its chapters?`)) deleteFic.mutate(fic.id);
            }} />
        ))}

        {fics?.length === 0 && !isLoading && (
          <div className="text-center text-[var(--color-text-muted)] py-12">
            {searchQuery ? 'No fics match' : 'Nothing here yet — import a fic from a forum or upload an EPUB/DOCX/PDF.'}
          </div>
        )}
      </div>
    </div>
  );
}

function FicCard({ fic, selected, showDelete, onOpen, onCheckUpdates, onTagClick, onDelete }: {
  fic: Fic;
  selected: boolean;
  showDelete: boolean;
  onOpen: () => void;
  onCheckUpdates: () => void;
  onTagClick: (name: string) => void;
  onDelete: () => void;
}) {
  const [showReview, setShowReview] = useState(false);
  const downloading = fic.downloadStatus === 'downloading';
  const progress = fic.downloadProgress;
  const pct = progress?.chaptersTotal
    ? Math.min(100, Math.round((progress.chaptersDone / progress.chaptersTotal) * 100))
    : null;
  const badge = fic.sourceType === 'xenforo' ? siteLabel(fic.site) : fic.sourceType.toUpperCase();

  return (
    <div ref={(el) => { if (el && selected) el.scrollIntoView({ block: 'nearest' }); }}
      className={`p-4 bg-[var(--color-surface)] rounded-lg border ${selected ? 'border-[var(--color-primary)]' : 'border-white/10'}`}>
      <div className="flex items-start gap-3">
        {fic.coverPath && (
          <img src={`/api/fanfic/${fic.id}/images/${fic.coverPath}`} alt=""
            className="w-12 h-16 object-cover rounded border border-white/10 shrink-0" />
        )}
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-2">
            <button onClick={onOpen}
              className="text-left text-base font-bold text-[var(--color-text)] hover:text-[var(--color-primary)] transition-colors">
              {fic.title}
            </button>
            <div className="flex gap-2 shrink-0">
              <FolderPicker fic={fic} />
              <button onClick={() => setShowReview(!showReview)}
                className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                title="Rate and review">Review</button>
              {fic.sourceType === 'xenforo' && !downloading && (
                <button onClick={onCheckUpdates}
                  className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                  title="Fetch new chapters">↻ Update</button>
              )}
              {showDelete && (
                <button onClick={onDelete} className="text-sm text-red-400 hover:text-red-300">Delete</button>
              )}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-sm text-[var(--color-text-muted)] mt-0.5">
            {fic.author && <span>{fic.author}</span>}
            {formatRating(fic.rating) && (
              <span className="text-amber-400" title={`Rated ${fic.rating}/5`}>{formatRating(fic.rating)}</span>
            )}
            {badge && (
              <span className="px-1.5 py-0.5 text-xs rounded border border-white/20">{badge}</span>
            )}
            {(fic.folderIds?.length ?? 0) === 0 && (
              <span className="px-1.5 py-0.5 text-xs rounded border border-amber-400/40 text-amber-300"
                title="Not sorted into any folder">Unsorted</span>
            )}
            {fic.chapterCount > 0 && <span>{fic.chapterCount} chapters</span>}
            {(fic.readCount ?? 0) > 0 && fic.chapterCount > 0 && (
              <span title="Chapters read">{fic.readCount}/{fic.chapterCount} read</span>
            )}
            {fic.wordCount > 0 && <span>{formatWords(fic.wordCount)}</span>}
            <span>added {formatDate(fic.createdAt)}</span>
          </div>

          {fic.tags && fic.tags.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-1.5">
              {fic.tags.map((name) => (
                <button key={name} onClick={() => onTagClick(name)}
                  className="px-1.5 py-0.5 text-xs rounded border border-white/15 text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-white/30 transition-colors"
                  title={`Filter library by "${name}"`}>
                  {name}
                </button>
              ))}
            </div>
          )}

          {fic.matchedChapters && fic.matchedChapters.length > 0 && (
            <div className="mt-1 text-xs text-[var(--color-text-muted)]">
              matches: {fic.matchedChapters.map((c) => c.title).join(' · ')}
            </div>
          )}

          {showReview && <ReviewEditor fic={fic} onClose={() => setShowReview(false)} />}

          {downloading && (
            <div className="mt-2">
              <div className="flex justify-between text-xs text-[var(--color-text-muted)] mb-1">
                <span>
                  {progress?.phase === 'index' ? 'Fetching index…'
                    : progress?.phase === 'updating' ? 'Checking for new chapters…'
                    : `Downloading chapters… ${progress?.chaptersDone ?? 0}${progress?.chaptersTotal ? ` / ${progress.chaptersTotal}` : ''}`}
                </span>
                {pct !== null && <span>{pct}%</span>}
              </div>
              <div className="h-1.5 bg-white/10 rounded overflow-hidden">
                <div className="h-full bg-[var(--color-primary)] transition-all"
                  style={{ width: pct !== null ? `${pct}%` : '100%', opacity: pct !== null ? 1 : 0.4 }} />
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
  // null = untouched; the stored review text arrives with the detail fetch
  const [text, setText] = useState<string | null>(null);
  const queryClient = useQueryClient();

  // List rows omit the review text — load it when the editor opens.
  const { data: detail } = useQuery({
    queryKey: ['fanfic', 'detail', fic.id],
    queryFn: () => api.fanfic.get(fic.id),
  });
  const value = text ?? detail?.review ?? '';

  const save = useMutation({
    mutationFn: () => api.fanfic.saveReview(fic.id, {
      rating,
      review: value.trim() || null,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['fanfic'] });
      onClose();
    },
  });

  return (
    <div className="mt-2 p-3 bg-black/20 rounded-lg border border-white/10">
      <div className="flex items-center gap-1 mb-2">
        {[1, 2, 3, 4, 5].map((n) => (
          <button key={n} onClick={() => setRating(rating === n ? null : n)}
            className={`text-xl leading-none ${rating !== null && n <= rating ? 'text-amber-400' : 'text-white/25 hover:text-white/50'}`}
            title={rating === n ? 'Clear rating' : `Rate ${n}/5`}>
            ★
          </button>
        ))}
        {rating !== null && (
          <span className="ml-1 text-xs text-[var(--color-text-muted)]">{rating}/5</span>
        )}
      </div>
      <textarea value={value} onChange={(e) => setText(e.target.value)}
        placeholder="Your overall thoughts on this fic…" rows={3}
        className="w-full bg-transparent text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none border border-white/10 rounded p-2 resize-y" />
      <div className="flex justify-end gap-2 mt-1">
        <button onClick={onClose}
          className="px-3 py-1 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]">Cancel</button>
        <button onClick={() => save.mutate()} disabled={save.isPending}
          className="px-3 py-1 text-sm bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50">
          {save.isPending ? 'Saving…' : 'Save'}
        </button>
      </div>
    </div>
  );
}
