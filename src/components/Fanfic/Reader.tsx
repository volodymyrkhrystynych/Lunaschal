import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { adjacentChapter, chapterIdsUpTo, groupChaptersByCategory, orderChapters } from '../../lib/fanfic';
import { useShortcuts, useShortcutScope } from '../../shortcuts/ShortcutProvider';

interface ReaderProps {
  ficId: string;
  initialChapterId?: string;
  onBack: () => void;
}

export function Reader({ ficId, initialChapterId, onBack }: ReaderProps) {
  const [chapterId, setChapterId] = useState<string | null>(initialChapterId ?? null);
  const [showCommentary, setShowCommentary] = useState(false);
  const [commentary, setCommentary] = useState('');
  const [commentarySaved, setCommentarySaved] = useState(false);
  const contentRef = useRef<HTMLDivElement>(null);
  const commentaryRef = useRef<HTMLTextAreaElement>(null);
  const queryClient = useQueryClient();
  const { setLevel } = useShortcuts();

  // Opening a fic (even by mouse) puts keyboard focus inside the reader so
  // W/S move between chapters instead of switching app tabs.
  useEffect(() => {
    setLevel(1);
  }, [setLevel]);

  const { data: fic } = useQuery({
    queryKey: ['fanfic', 'fic', ficId],
    queryFn: () => api.fanfic.get(ficId),
  });

  const { data: chapters } = useQuery({
    queryKey: ['fanfic', 'chapters', ficId],
    queryFn: () => api.fanfic.chapters(ficId),
  });

  const isPdf = fic?.sourceType === 'pdf';

  // Pick the chapter to show: explicit target > last read > first chapter.
  useEffect(() => {
    if (chapterId || isPdf || !fic || !chapters?.length) return;
    const lastRead = fic.lastReadChapterId && chapters.find((c) => c.id === fic.lastReadChapterId);
    setChapterId(lastRead ? lastRead.id : orderChapters(chapters)[0].id);
  }, [chapterId, isPdf, fic, chapters]);

  const { data: chapter } = useQuery({
    queryKey: ['fanfic', 'chapter', chapterId],
    queryFn: () => api.fanfic.chapter(chapterId!),
    enabled: !!chapterId,
  });

  const saveProgress = useMutation({
    mutationFn: (chId: string) => api.fanfic.saveProgress(ficId, chId),
    // Opening a chapter marks it read server-side — refresh the sidebar too.
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['fanfic', 'fic', ficId] });
      queryClient.invalidateQueries({ queryKey: ['fanfic', 'chapters', ficId] });
    },
  });

  const setRead = useMutation({
    mutationFn: ({ ids, read }: { ids: string[]; read: boolean }) =>
      api.fanfic.setRead(ficId, ids, read),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['fanfic'] }),
  });

  useEffect(() => {
    if (chapterId) {
      saveProgress.mutate(chapterId);
      contentRef.current?.scrollTo({ top: 0 });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chapterId]);

  const saveCommentary = useMutation({
    mutationFn: async (text: string) => {
      const entry = await api.journal.create({ content: text });
      await api.fanfic.linkJournal(ficId, entry.id, isPdf ? undefined : chapterId ?? undefined);
      return entry;
    },
    onSuccess: () => {
      setCommentary('');
      setCommentarySaved(true);
      setTimeout(() => setCommentarySaved(false), 3000);
      queryClient.invalidateQueries({ queryKey: ['journal'] });
    },
  });

  const groups = useMemo(() => groupChaptersByCategory(chapters ?? []), [chapters]);
  const prev = chapterId && chapters ? adjacentChapter(chapters, chapterId, -1) : null;
  const next = chapterId && chapters ? adjacentChapter(chapters, chapterId, 1) : null;

  useShortcutScope(1, {
    next: () => { if (next) setChapterId(next.id); },
    prev: () => { if (prev) setChapterId(prev.id); },
    drillOut: () => { onBack(); return true; },
    scrollDown: () => contentRef.current?.scrollBy({ top: 120, behavior: 'smooth' }),
    scrollUp: () => contentRef.current?.scrollBy({ top: -120, behavior: 'smooth' }),
    annotate: () => {
      setShowCommentary(true);
      // autoFocus covers first open; refocus when the panel is already open
      setTimeout(() => commentaryRef.current?.focus(), 0);
    },
  });

  const chapterNav = (position: 'top' | 'bottom') => (
    <div className={`flex items-center justify-between gap-2 ${position === 'top' ? 'mb-4' : 'mt-8'}`}>
      <button onClick={() => prev && setChapterId(prev.id)} disabled={!prev}
        className="px-3 py-1.5 text-sm border border-white/20 text-[var(--color-text)] rounded hover:bg-white/10 disabled:opacity-30 transition-colors">
        ← {prev ? prev.title : 'Previous'}
      </button>
      <button onClick={() => next && setChapterId(next.id)} disabled={!next}
        className="px-3 py-1.5 text-sm border border-white/20 text-[var(--color-text)] rounded hover:bg-white/10 disabled:opacity-30 transition-colors">
        {next ? next.title : 'Next'} →
      </button>
    </div>
  );

  return (
    <div className="flex-1 flex overflow-hidden">
      {/* Chapter sidebar */}
      {!isPdf && (
        <aside className="w-64 border-r border-white/10 bg-[var(--color-surface)] flex flex-col shrink-0">
          <div className="p-3 border-b border-white/10">
            <button onClick={onBack}
              className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]">← Library</button>
            <div className="mt-1 font-semibold text-[var(--color-text)] leading-snug">{fic?.title}</div>
            {fic?.author && <div className="text-sm text-[var(--color-text-muted)]">{fic.author}</div>}
          </div>
          <div className="flex-1 overflow-y-auto p-2">
            {groups.map(([category, chs]) => (
              <div key={category} className="mb-2">
                {groups.length > 1 && (
                  <div className="px-2 py-1 text-xs uppercase tracking-wide text-[var(--color-text-muted)]">{category}</div>
                )}
                {chs.map((ch) => (
                  <div key={ch.id}
                    ref={(el) => { if (el && ch.id === chapterId) el.scrollIntoView({ block: 'nearest' }); }}
                    className={`flex items-center rounded transition-colors ${
                      ch.id === chapterId
                        ? 'bg-[var(--color-primary)]/20'
                        : 'hover:bg-white/10'
                    }`}>
                    <button onClick={() => setChapterId(ch.id)}
                      className={`flex-1 min-w-0 text-left px-2 py-1.5 text-sm truncate ${
                        ch.id === chapterId
                          ? 'text-[var(--color-primary)]'
                          : ch.isRead
                            ? 'text-[var(--color-text)] opacity-60'
                            : 'text-[var(--color-text)]'
                      }`}
                      title={ch.title}>
                      {ch.title}
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        if (e.shiftKey && chapters && !ch.isRead) {
                          setRead.mutate({ ids: chapterIdsUpTo(chapters, ch.id), read: true });
                        } else {
                          setRead.mutate({ ids: [ch.id], read: !ch.isRead });
                        }
                      }}
                      className={`px-2 py-1.5 text-xs shrink-0 transition-colors ${
                        ch.isRead
                          ? 'text-green-400/80 hover:text-green-300'
                          : 'text-white/25 hover:text-white/60'
                      }`}
                      title={ch.isRead
                        ? 'Mark unread'
                        : 'Mark read — shift-click marks everything up to here'}>
                      ✓
                    </button>
                  </div>
                ))}
              </div>
            ))}
          </div>
        </aside>
      )}

      {/* Content pane */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {isPdf ? (
          <>
            <div className="p-2 border-b border-white/10 flex items-center gap-3 bg-[var(--color-surface)]">
              <button onClick={onBack}
                className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]">← Library</button>
              <span className="font-semibold text-[var(--color-text)]">{fic?.title}</span>
            </div>
            <iframe src={`/api/fanfic/${ficId}/pdf`} title={fic?.title} className="flex-1 w-full border-0" />
          </>
        ) : (
          <div ref={contentRef} className="flex-1 overflow-y-auto">
            <div className="max-w-3xl mx-auto px-6 py-6">
              {chapterNav('top')}
              {chapters && chapters.length === 0 ? (
                <div className="text-[var(--color-text-muted)] py-12 text-center">
                  <p className="mb-2">No chapters were downloaded for this fic.</p>
                  <p className="text-sm">
                    {fic?.downloadError
                      ? fic.downloadError
                      : 'Try "↻ Update" in the library, or re-import the fic\'s URL to retry the download.'}
                  </p>
                </div>
              ) : chapter ? (
                <>
                  <h2 className="text-xl font-bold text-[var(--color-text)] mb-1">{chapter.title}</h2>
                  <div className="text-sm text-[var(--color-text-muted)] mb-6 flex gap-3">
                    <span>{chapter.wordCount} words</span>
                    {chapter.sourceUrl && (
                      <a href={chapter.sourceUrl} target="_blank" rel="noreferrer"
                        className="underline hover:text-[var(--color-text)]">view on forum</a>
                    )}
                  </div>
                  <div className="fanfic-prose" dangerouslySetInnerHTML={{ __html: chapter.contentHtml }} />
                  {chapterNav('bottom')}
                </>
              ) : (
                <div className="text-[var(--color-text-muted)]">Loading chapter…</div>
              )}
            </div>
          </div>
        )}

        {/* Commentary panel */}
        <div className="border-t border-white/10 bg-[var(--color-surface)]">
          <button onClick={() => setShowCommentary(!showCommentary)}
            className="w-full px-4 py-2 text-left text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors">
            {showCommentary ? '▾' : '▸'} Write commentary
            {commentarySaved && <span className="ml-2 text-green-400">saved to journal ✓</span>}
          </button>
          {showCommentary && (
            <div className="px-4 pb-3">
              <textarea ref={commentaryRef} value={commentary} onChange={(e) => setCommentary(e.target.value)}
                rows={3} autoFocus
                onKeyDown={(e) => {
                  if (e.key === 'Escape') {
                    setShowCommentary(false);
                    return;
                  }
                  // Enter submits; Shift+Enter inserts a newline
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    if (commentary.trim() && !saveCommentary.isPending) {
                      saveCommentary.mutate(commentary.trim());
                      setShowCommentary(false);
                      e.currentTarget.blur();
                    }
                  }
                }}
                placeholder={isPdf
                  ? `Your thoughts on ${fic?.title ?? 'this fic'}… (saved as a journal entry linked to the fic)`
                  : `Your thoughts on ${chapter?.title ?? 'this chapter'}… (saved as a journal entry linked to this chapter)`}
                className="w-full bg-[var(--color-bg)] text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] resize-none focus:outline-none border border-white/10 rounded p-2 mb-2" />
              {saveCommentary.isError && (
                <div className="mb-2 px-3 py-2 bg-red-500/10 border border-red-500/20 rounded text-sm text-red-400">
                  {(saveCommentary.error as Error).message}
                </div>
              )}
              <div className="flex justify-end">
                <button onClick={() => saveCommentary.mutate(commentary.trim())}
                  disabled={!commentary.trim() || saveCommentary.isPending}
                  className="px-3 py-1 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50">
                  {saveCommentary.isPending ? 'Saving…' : 'Save to journal'}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
