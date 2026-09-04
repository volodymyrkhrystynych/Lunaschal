import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import type { FicBookmark } from '../../hooks/api';
import {
  useFanficProgress,
  useFanficSetRead,
} from '../../offline/mutationDefaults';
import { FicDownloadButton } from './FicDownloadButton';
import { FolderPicker } from './Folders';
import { BookmarkMenu } from './BookmarkMenu';
import {
  adjacentChapter,
  chapterIdsUpTo,
  groupChaptersByCategory,
  orderChapters,
} from '../../lib/fanfic';
import {
  resolveInitialChapter,
  scrollFraction,
  scrollTopForFraction,
} from '../../lib/fanficBookmarks';
import {
  useShortcuts,
  useShortcutScope,
} from '../../shortcuts/ShortcutProvider';
import {
  READING_FONT_SIZE_STEP,
  getStoredFontSize,
  getStoredReadingFontSize,
  setStoredReadingFontSize,
} from '../../lib/fontSize';
import { useMasterDetail } from '@/hooks/useMasterDetail';
import { MasterDetailBack } from '@/components/MasterDetailBack';
import { useRecorder } from '../../hooks/useRecorder';
import { captureFicCommentary } from '../../offline/recordingQueue';

interface ReaderProps {
  ficId: string;
  initialChapterId?: string;
  onBack: () => void;
}

export function Reader({ ficId, initialChapterId, onBack }: ReaderProps) {
  const [chapterId, setChapterId] = useState<string | null>(
    initialChapterId ?? null
  );
  const [commentary, setCommentary] = useState('');
  // What to show beside the Commentary header after a save. Two different
  // sentences, because the two halves of this panel finish differently: typed
  // commentary is an entry the moment it is saved, a recording is an entry with
  // its text still on the way.
  const [commentarySaved, setCommentarySaved] = useState('');
  const [recordingNotice, setRecordingNotice] = useState('');
  const [recordingStarting, setRecordingStarting] = useState(false);
  const [showCommentary, setShowCommentary] = useState(false);
  const [navVisible, setNavVisible] = useState(true);
  const [fontSize, setFontSize] = useState(getStoredReadingFontSize);
  const { isMobile, showList, showDetail, openDetail, openList } =
    useMasterDetail();
  const contentRef = useRef<HTMLDivElement>(null);
  const commentaryRef = useRef<HTMLTextAreaElement>(null);
  const queryClient = useQueryClient();
  const { setLevel, level } = useShortcuts();

  useEffect(() => {
    api.fanfic.markOpened(ficId).catch(() => {
      // Reading remains available if this best-effort history write fails.
    });
  }, [ficId]);

  // Speak the commentary instead of typing it. This is the Journal button's
  // contract, not a dictation box's: **stopping the recording is the save**.
  // The clip goes to the durable store, is uploaded as a journal entry linked
  // to this chapter, and the transcript, the polish and the title all arrive
  // on that entry minutes later on their own.
  //
  // It used to transcribe in the browser, append the text to the box below and
  // post that — which meant the audio lived only in memory, so a failed
  // transcription (or a locked phone mid-thought) lost the commentary outright.
  // Reacting to a chapter is the case that most wants to stay hands-free: you
  // are mid-read, not looking at the box. So no transcript comes back here; the
  // textarea is left alone for what was typed into it.
  const recorder = useRecorder(() => undefined, undefined, {
    onNotice: setRecordingNotice,
    onRecording: rec => {
      // Not awaited — see captureFicCommentary. The audio is on disk until the
      // server confirms it, and the entry is already in the journal feed from
      // the upload's optimistic insert.
      void captureFicCommentary(queryClient, rec).catch(() => undefined);
      setCommentarySaved('recording saved — the transcript follows ✓');
      setTimeout(() => setCommentarySaved(''), 3000);
    },
  });

  const toggleRecording = () => {
    if (recorder.status === 'recording') {
      recorder.stop();
      return;
    }
    if (recorder.status !== 'idle' || recordingStarting) return;
    setRecordingNotice('');
    // The chapter is captured here, at the first chunk, and stored beside the
    // audio: W/S moves the reader on while a thought is still being spoken, and
    // resolving the link at upload time would file the commentary under
    // whatever chapter was open by then. A PDF fic has no chapters to link to.
    // getUserMedia and the first IndexedDB write both happen before the shared
    // recorder can report `recording`. Reflect that setup immediately: without
    // it the browser's microphone indicator came on while this button still
    // looked untouched, and another tap was silently ignored by useRecorder's
    // duplicate-start guard.
    setRecordingStarting(true);
    void recorder
      .start('transcribe', {
        durable: true,
        fic: {
          ficId,
          ...(!isPdf && chapterId ? { chapterId } : {}),
        },
      })
      .finally(() => setRecordingStarting(false));
  };

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

  const { data: bookmarks } = useQuery({
    queryKey: ['fanfic', 'bookmarks', ficId],
    queryFn: () => api.fanfic.bookmarks.list(ficId),
  });
  const continueBookmark = bookmarks?.find(b => b.type === 'continue') ?? null;
  const favorites = useMemo(
    () => bookmarks?.filter(b => b.type === 'favorite') ?? [],
    [bookmarks]
  );

  const isPdf = fic?.sourceType === 'pdf';

  // Desktop keeps the power-user `navVisible` collapse; mobile shows exactly one
  // of chapter list / reading pane. PDFs have no chapter list, so force the pane.
  const navShown = !isPdf && (isMobile ? showList : navVisible);
  const contentShown = !isMobile || isPdf || showDetail;

  // Scroll fraction to restore once the chapter a continue-bookmark or a
  // favorites jump points at has actually rendered (consumed once, then
  // cleared — a manual chapter switch afterwards resets to the top as usual).
  const pendingScrollRef = useRef<{
    chapterId: string;
    fraction: number;
  } | null>(null);

  // Pick the chapter to show: explicit target (chapterId already set from the
  // initialChapterId prop) > continue bookmark > last read > first chapter.
  // Waits on the bookmarks query too so a continue bookmark isn't missed by a
  // last-read fallback that resolves first.
  useEffect(() => {
    if (chapterId || isPdf || !fic || !chapters?.length || !bookmarks) return;
    const picked = resolveInitialChapter(orderChapters(chapters), {
      continueChapterId: continueBookmark?.chapterId,
      lastReadChapterId: fic.lastReadChapterId,
    });
    if (!picked) return;
    if (continueBookmark && picked.id === continueBookmark.chapterId) {
      pendingScrollRef.current = {
        chapterId: picked.id,
        fraction: continueBookmark.scrollPosition,
      };
    }
    setChapterId(picked.id);
  }, [chapterId, isPdf, fic, chapters, bookmarks, continueBookmark]);

  const { data: chapter } = useQuery({
    queryKey: ['fanfic', 'chapter', chapterId],
    queryFn: () => api.fanfic.chapter(chapterId!),
    enabled: !!chapterId,
  });

  // Offline-queueable: idempotent progress/read writes; invalidation lives in
  // the registered mutation defaults (keyed off ficId in the variables).
  const saveProgress = useFanficProgress();
  const setRead = useFanficSetRead();

  useEffect(() => {
    if (chapterId) {
      saveProgress.mutate({ ficId, chapterId });
      contentRef.current?.scrollTo({ top: 0 });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chapterId]);

  // Restore a pending bookmark's scroll position once its chapter has
  // actually rendered (scrollHeight isn't known until then). On mobile the
  // content pane doesn't mount until the user backs out of the chapter list
  // (contentShown), so contentRef.current is still null the first time this
  // runs — refs aren't reactive, so contentShown must be a dep too, or the
  // effect never gets a second chance once the pane finally exists.
  useEffect(() => {
    const pending = pendingScrollRef.current;
    const el = contentRef.current;
    if (!chapter || !el || !pending || pending.chapterId !== chapter.id) return;
    el.scrollTo({
      top: scrollTopForFraction(
        pending.fraction,
        el.scrollHeight,
        el.clientHeight
      ),
    });
    pendingScrollRef.current = null;
  }, [chapter, contentShown]);

  // Both mutations used to fail silently on error — a dropped bookmark create
  // or delete looked identical to "nothing happened", which is exactly what
  // made a real failure indistinguishable from the mistap this file also
  // guards against below.
  const [bookmarkError, setBookmarkError] = useState<string | null>(null);

  const createBookmark = useMutation({
    mutationFn: (data: {
      type: 'favorite' | 'continue';
      scrollPosition: number;
    }) =>
      api.fanfic.bookmarks.create(ficId, {
        chapterId: chapterId!,
        ...data,
      }),
    onSuccess: () => {
      setBookmarkError(null);
      queryClient.invalidateQueries({
        queryKey: ['fanfic', 'bookmarks', ficId],
      });
    },
    onError: (e: Error) => setBookmarkError(e.message),
  });

  const deleteBookmark = useMutation({
    mutationFn: (bookmarkId: string) => api.fanfic.bookmarks.delete(bookmarkId),
    onSuccess: () => {
      setBookmarkError(null);
      queryClient.invalidateQueries({
        queryKey: ['fanfic', 'bookmarks', ficId],
      });
    },
    onError: (e: Error) => setBookmarkError(e.message),
  });

  const [showBookmarkMenu, setShowBookmarkMenu] = useState(false);

  // Calculate bookmark indicator positions so they stay in the text as it scrolls.
  const [bookmarkIndicators, setBookmarkIndicators] = useState<
    Array<{ id: string; type: 'favorite' | 'continue'; top: number }>
  >([]);

  // Same non-reactive-ref issue as the scroll restore above: on mobile
  // contentRef.current is null until contentShown flips true, so that has to
  // be a dep or the indicator lines never get computed once the pane mounts.
  useLayoutEffect(() => {
    const el = contentRef.current;
    if (!chapter || !el || !bookmarks?.length) {
      setBookmarkIndicators([]);
      return;
    }
    const range = el.scrollHeight - el.clientHeight;
    if (range <= 0) {
      setBookmarkIndicators([]);
      return;
    }
    const indicators = bookmarks
      .filter(bm => bm.chapterId === chapterId)
      .map(bm => ({
        id: bm.id,
        type: bm.type,
        top: bm.scrollPosition * range,
      }));
    setBookmarkIndicators(indicators);
  }, [chapter, bookmarks, chapterId, fontSize, contentShown]);

  // Recalculate when the window resizes so lines stay at the correct positions.
  useEffect(() => {
    const onResize = () => {
      const el = contentRef.current;
      if (!chapter || !el || !bookmarks?.length) return;
      const range = el.scrollHeight - el.clientHeight;
      if (range <= 0) {
        setBookmarkIndicators([]);
        return;
      }
      const indicators = bookmarks
        .filter(bm => bm.chapterId === chapterId)
        .map(bm => ({
          id: bm.id,
          type: bm.type,
          top: bm.scrollPosition * range,
        }));
      setBookmarkIndicators(indicators);
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [chapter, bookmarks, chapterId]);

  const pickBookmark = (type: 'favorite' | 'continue') => {
    const el = contentRef.current;
    if (!el) return;
    createBookmark.mutate({
      type,
      scrollPosition: scrollFraction(
        el.scrollTop,
        el.scrollHeight,
        el.clientHeight
      ),
    });
  };

  const jumpToBookmark = (bm: FicBookmark) => {
    if (bm.chapterId === chapterId) {
      const el = contentRef.current;
      if (el) {
        el.scrollTo({
          top: scrollTopForFraction(
            bm.scrollPosition,
            el.scrollHeight,
            el.clientHeight
          ),
          behavior: 'smooth',
        });
      }
    } else {
      pendingScrollRef.current = {
        chapterId: bm.chapterId,
        fraction: bm.scrollPosition,
      };
      setChapterId(bm.chapterId);
    }
    openDetail();
  };

  const [showFavorites, setShowFavorites] = useState(false);
  const favButtonRef = useRef<HTMLButtonElement>(null);
  const favMenuRef = useRef<HTMLDivElement>(null);
  const [favMenuPos, setFavMenuPos] = useState<{
    top: number;
    left: number;
  } | null>(null);

  useLayoutEffect(() => {
    if (!showFavorites) {
      setFavMenuPos(null);
      return;
    }
    const button = favButtonRef.current;
    const menu = favMenuRef.current;
    if (!button || !menu) return;
    const margin = 8;
    const buttonRect = button.getBoundingClientRect();
    const left = Math.min(
      Math.max(buttonRect.left, margin),
      window.innerWidth - menu.offsetWidth - margin
    );
    setFavMenuPos({ top: buttonRect.bottom + 4, left });
  }, [showFavorites, favorites.length]);

  const saveCommentary = useMutation({
    mutationFn: async (text: string) => {
      // createFromVoice, not create: commentary is dictated as often as
      // typed (the mic button above appends into the same box either way),
      // and sending it as raw_content is what queues Journal's background
      // polish — a misheard character name gets fixed the same way any
      // other journal entry's does, instead of the commentary skipping it.
      const entry = await api.journal.createFromVoice(text);
      await api.fanfic.linkJournal(
        ficId,
        entry.id,
        isPdf ? undefined : (chapterId ?? undefined)
      );
      return entry;
    },
    onSuccess: () => {
      setCommentary('');
      setCommentarySaved('saved to journal ✓');
      setTimeout(() => setCommentarySaved(''), 3000);
      queryClient.invalidateQueries({ queryKey: ['journal'] });
    },
  });

  const groups = useMemo(
    () => groupChaptersByCategory(chapters ?? []),
    [chapters]
  );
  const prev =
    chapterId && chapters ? adjacentChapter(chapters, chapterId, -1) : null;
  const next =
    chapterId && chapters ? adjacentChapter(chapters, chapterId, 1) : null;

  const annotate = () => {
    setShowCommentary(true);
    setTimeout(() => commentaryRef.current?.focus(), 0);
  };

  const adjustFontSize = (delta: number) => {
    setFontSize(px => setStoredReadingFontSize(px + delta));
  };

  // Level 1: chapter list — W/S switch chapters, A back to library, D enters the chapter.
  useShortcutScope(1, {
    next: () => {
      if (next) setChapterId(next.id);
    },
    prev: () => {
      if (prev) setChapterId(prev.id);
    },
    drillOut: () => {
      onBack();
      return true;
    },
    annotate,
    fontUp: () => adjustFontSize(READING_FONT_SIZE_STEP),
    fontDown: () => adjustFontSize(-READING_FONT_SIZE_STEP),
    toggleList: () => setNavVisible(v => !v),
  });

  // Level 2: reading — no next/prev, so W/S fall back to scrolling the prose;
  // A ascends back to the chapter list by default.
  useShortcutScope(2, {
    scrollDown: () =>
      contentRef.current?.scrollBy({ top: 120, behavior: 'smooth' }),
    scrollUp: () =>
      contentRef.current?.scrollBy({ top: -120, behavior: 'smooth' }),
    annotate,
  });

  const chapterNav = (position: 'top' | 'bottom') => (
    <div
      className={`flex items-center justify-between gap-2 ${position === 'top' ? 'mb-4' : 'mt-8'}`}
    >
      <button
        onClick={() => prev && setChapterId(prev.id)}
        disabled={!prev}
        className="px-3 py-1.5 text-sm border border-white/20 text-[var(--color-text)] rounded hover:bg-white/10 disabled:opacity-30 transition-colors"
      >
        ← {prev ? prev.title : 'Previous'}
      </button>
      <button
        onClick={() => next && setChapterId(next.id)}
        disabled={!next}
        className="px-3 py-1.5 text-sm border border-white/20 text-[var(--color-text)] rounded hover:bg-white/10 disabled:opacity-30 transition-colors"
      >
        {next ? next.title : 'Next'} →
      </button>
    </div>
  );

  return (
    <>
      <div className="flex-1 flex overflow-hidden">
        {/* Chapter sidebar */}
        {navShown && (
          <aside
            data-reader-nav
            className={`${isMobile ? 'w-full' : 'w-64 shrink-0'} border-r border-white/10 bg-[var(--color-surface)] flex flex-col`}
          >
            <div className="p-3 border-b border-white/10">
              <button
                onClick={onBack}
                className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              >
                ← Library
              </button>
              <div className="mt-1 flex items-start gap-1.5">
                <div className="font-semibold text-[var(--color-text)] leading-snug">
                  {fic?.title}
                </div>
                {continueBookmark && (
                  <button
                    onClick={() => {
                      if (confirm('Clear the continue bookmark for this fic?'))
                        deleteBookmark.mutate(continueBookmark.id);
                    }}
                    className="shrink-0 mt-0.5 text-xs text-[var(--color-text-muted)] hover:text-red-400"
                    title="Clear continue bookmark"
                  >
                    ▶✕
                  </button>
                )}
              </div>
              {fic?.author && (
                <div className="text-sm text-[var(--color-text-muted)]">
                  {fic.author}
                </div>
              )}
              {fic && (
                <div className="mt-1 flex items-center gap-2">
                  <FolderPicker fic={fic} />
                  {favorites.length > 0 && (
                    <button
                      ref={favButtonRef}
                      onClick={() => setShowFavorites(v => !v)}
                      className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                      title="Favorite bookmarks"
                    >
                      ★ Favorites ({favorites.length})
                    </button>
                  )}
                </div>
              )}
              {showFavorites &&
                createPortal(
                  <>
                    <div
                      className="fixed inset-0 z-40"
                      onClick={() => setShowFavorites(false)}
                    />
                    <div
                      ref={favMenuRef}
                      className="fixed z-50 min-w-56 max-w-[calc(100vw-16px)] p-1 bg-[var(--color-surface)] border border-white/15 rounded-lg shadow-xl"
                      style={{
                        top: favMenuPos?.top ?? 0,
                        left: favMenuPos?.left ?? 0,
                        visibility: favMenuPos ? 'visible' : 'hidden',
                      }}
                    >
                      {favorites.map(bm => (
                        <div
                          key={bm.id}
                          className="flex items-center rounded hover:bg-white/5"
                        >
                          <button
                            onClick={() => {
                              jumpToBookmark(bm);
                              setShowFavorites(false);
                            }}
                            className="flex-1 min-w-0 text-left px-2 py-1.5 text-sm text-[var(--color-text)] truncate"
                            title={bm.chapterTitle}
                          >
                            {bm.chapterTitle}
                          </button>
                          <button
                            onClick={() => {
                              if (
                                confirm(`Remove favorite "${bm.chapterTitle}"?`)
                              )
                                deleteBookmark.mutate(bm.id);
                            }}
                            className="px-2 py-1.5 text-xs shrink-0 text-white/25 hover:text-red-400"
                            title="Remove favorite"
                          >
                            ✕
                          </button>
                        </div>
                      ))}
                    </div>
                  </>,
                  document.body
                )}
              <FicDownloadButton chapters={chapters} />
            </div>
            <div className="flex-1 overflow-y-auto overflow-x-hidden p-2">
              {groups.map(([category, chs]) => (
                <div key={category} className="mb-2">
                  {groups.length > 1 && (
                    <div className="px-2 py-1 text-xs uppercase tracking-wide text-[var(--color-text-muted)]">
                      {category}
                    </div>
                  )}
                  {chs.map(ch => (
                    <div
                      key={ch.id}
                      ref={el => {
                        if (el && ch.id === chapterId)
                          el.scrollIntoView({ block: 'nearest' });
                      }}
                      className={`flex items-center rounded transition-colors ${
                        ch.id === chapterId
                          ? 'bg-[var(--color-primary)]/20'
                          : 'hover:bg-white/10'
                      } ${level === 1 && ch.id === chapterId ? 'ring-1 ring-[var(--color-primary)]' : ''}`}
                    >
                      <button
                        onClick={() => {
                          setChapterId(ch.id);
                          openDetail();
                        }}
                        className={`flex-1 min-w-0 text-left px-2 py-1.5 text-sm truncate ${
                          ch.id === chapterId
                            ? 'text-[var(--color-primary)]'
                            : ch.isRead
                              ? 'text-[var(--color-text)] opacity-60'
                              : 'text-[var(--color-text)]'
                        }`}
                        title={ch.title}
                      >
                        {ch.title}
                      </button>
                      <button
                        onClick={e => {
                          e.stopPropagation();
                          if (e.shiftKey && chapters && !ch.isRead) {
                            setRead.mutate({
                              ficId,
                              ids: chapterIdsUpTo(chapters, ch.id),
                              read: true,
                            });
                          } else {
                            setRead.mutate({
                              ficId,
                              ids: [ch.id],
                              read: !ch.isRead,
                            });
                          }
                        }}
                        className={`px-2 py-1.5 text-xs shrink-0 transition-colors ${
                          ch.isRead
                            ? 'text-green-400/80 hover:text-green-300'
                            : 'text-white/25 hover:text-white/60'
                        }`}
                        title={
                          ch.isRead
                            ? 'Mark unread'
                            : 'Mark read — shift-click marks everything up to here'
                        }
                      >
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
        {contentShown && (
          <div className="flex-1 flex flex-col overflow-hidden">
            {!isPdf && <MasterDetailBack onClick={openList} label="Chapters" />}
            {isPdf ? (
              <>
                <div className="p-2 border-b border-white/10 flex items-center gap-3 bg-[var(--color-surface)]">
                  <button
                    onClick={onBack}
                    className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                  >
                    ← Library
                  </button>
                  <span className="font-semibold text-[var(--color-text)]">
                    {fic?.title}
                  </span>
                </div>
                <iframe
                  src={`/api/fanfic/${ficId}/pdf`}
                  title={fic?.title}
                  className="flex-1 w-full border-0"
                />
              </>
            ) : (
              <div
                ref={contentRef}
                className={`flex-1 overflow-y-auto ${level >= 2 ? 'ring-1 ring-inset ring-[var(--color-primary)]' : ''}`}
                style={{ position: 'relative' }}
              >
                <div className="max-w-3xl mx-auto px-6 py-6 relative">
                  {bookmarkIndicators.map(ind => (
                    <div
                      key={ind.id}
                      className="absolute left-0 right-0 h-0.5 pointer-events-none"
                      style={{
                        top: `${ind.top}px`,
                        backgroundColor:
                          ind.type === 'favorite' ? '#ef4444' : '#22c55e',
                        opacity: 0.7,
                      }}
                    />
                  ))}
                  {chapterNav('top')}
                  {chapters && chapters.length === 0 ? (
                    <div className="text-[var(--color-text-muted)] py-12 text-center">
                      <p className="mb-2">
                        No chapters were downloaded for this fic.
                      </p>
                      <p className="text-sm">
                        {fic?.downloadError
                          ? fic.downloadError
                          : 'Try "↻ Update" in the library, or re-import the fic\'s URL to retry the download.'}
                      </p>
                    </div>
                  ) : chapter ? (
                    <>
                      <h2 className="text-xl font-bold text-[var(--color-text)] mb-1 break-words">
                        {chapter.title}
                      </h2>
                      <div className="text-sm text-[var(--color-text-muted)] mb-6 flex flex-wrap gap-3">
                        <span>{chapter.wordCount} words</span>
                        {chapter.createdAt && (
                          <span>
                            created {formatChapterDate(chapter.createdAt)}
                          </span>
                        )}
                        {chapter.updatedAt && (
                          <span>
                            updated {formatChapterDate(chapter.updatedAt)}
                          </span>
                        )}
                        {fontSize !== getStoredFontSize() && (
                          <span>{fontSize}px</span>
                        )}
                        {chapter.sourceUrl && (
                          <a
                            href={chapter.sourceUrl}
                            target="_blank"
                            rel="noreferrer"
                            className="underline hover:text-[var(--color-text)]"
                          >
                            view on forum
                          </a>
                        )}
                      </div>
                      <div
                        className="fanfic-prose"
                        style={{ fontSize: `${fontSize}px` }}
                        dangerouslySetInnerHTML={{
                          __html: chapter.contentHtml,
                        }}
                      />
                      {chapterNav('bottom')}
                    </>
                  ) : (
                    <div className="text-[var(--color-text-muted)]">
                      Loading chapter…
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Commentary panel */}
            <div className="border-t border-white/10 bg-[var(--color-surface)]">
              <div className="flex items-center justify-between px-4 py-2">
                <button
                  onClick={() => setShowCommentary(!showCommentary)}
                  className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] flex items-center gap-1"
                >
                  {showCommentary ? '▾' : '▸'} Commentary
                  {commentarySaved && (
                    <span className="text-green-400">{commentarySaved}</span>
                  )}
                </button>
                <button
                  onClick={() => setShowBookmarkMenu(true)}
                  className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                >
                  Bookmark
                </button>
              </div>
              {bookmarkError && (
                <div className="mx-4 mb-2 px-3 py-2 bg-red-500/10 border border-red-500/20 rounded text-sm text-red-400">
                  {bookmarkError}
                </div>
              )}
              {showCommentary && (
                <div className="px-4 pb-3">
                  <textarea
                    ref={commentaryRef}
                    value={commentary}
                    onChange={e => setCommentary(e.target.value)}
                    rows={3}
                    onKeyDown={e => {
                      if (e.key === 'Escape') {
                        e.currentTarget.blur();
                        return;
                      }
                      // Enter submits; Shift+Enter inserts a newline
                      if (e.key === 'Enter' && !e.shiftKey) {
                        e.preventDefault();
                        if (commentary.trim() && !saveCommentary.isPending) {
                          saveCommentary.mutate(commentary.trim());
                          e.currentTarget.blur();
                        }
                      }
                    }}
                    placeholder={
                      isPdf
                        ? `Your thoughts on ${fic?.title ?? 'this fic'}… (saved as a journal entry linked to the fic)`
                        : `Your thoughts on ${chapter?.title ?? 'this chapter'}… (saved as a journal entry linked to this chapter)`
                    }
                    className="w-full bg-[var(--color-bg)] text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] resize-none focus:outline-none border border-white/10 rounded p-2 mb-2"
                  />
                  {saveCommentary.isError && (
                    <div className="mb-2 px-3 py-2 bg-red-500/10 border border-red-500/20 rounded text-sm text-red-400">
                      {(saveCommentary.error as Error).message}
                    </div>
                  )}
                  {recordingNotice && (
                    <p className="mb-2 text-xs text-[var(--color-text-muted)]">
                      {recordingNotice}
                    </p>
                  )}
                  {recorder.error && (
                    <p className="mb-2 text-xs text-red-400">
                      {recorder.error}
                    </p>
                  )}
                  <div className="flex justify-end gap-2">
                    {recorder.status === 'recording' && (
                      <span className="mr-auto self-center text-xs text-[var(--color-text-muted)]">
                        Stop to save — it transcribes itself.
                      </span>
                    )}
                    <button
                      onClick={toggleRecording}
                      // Not gated on being online any more: the clip is stored
                      // on the device and uploaded when the server is back, so
                      // commentary on a chapter read offline is still kept.
                      // Only the moment it is being handed over is blocked, and
                      // while recording this button is the only way to stop.
                      disabled={
                        recorder.status === 'saving' || recordingStarting
                      }
                      title={
                        recordingStarting
                          ? 'Starting microphone'
                          : recorder.status === 'recording'
                            ? 'Stop recording'
                            : 'Record commentary — it saves and transcribes itself'
                      }
                      className={`px-3 py-1 rounded disabled:opacity-50 ${
                        recorder.status === 'recording'
                          ? 'bg-red-600 hover:bg-red-700 text-white'
                          : 'bg-white/10 hover:bg-white/20 text-[var(--color-text)]'
                      }`}
                    >
                      {recordingStarting
                        ? 'Starting…'
                        : recorder.status === 'recording'
                          ? '■ Stop'
                          : recorder.status === 'saving'
                            ? 'Saving…'
                            : '🎤'}
                    </button>
                    <button
                      onClick={() => saveCommentary.mutate(commentary.trim())}
                      disabled={!commentary.trim() || saveCommentary.isPending}
                      className="px-3 py-1 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
                    >
                      {saveCommentary.isPending ? 'Saving…' : 'Save to journal'}
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
      {showBookmarkMenu && (
        <BookmarkMenu
          onPick={pickBookmark}
          onClose={() => setShowBookmarkMenu(false)}
        />
      )}
    </>
  );
}

function formatChapterDate(date: string) {
  return new Intl.DateTimeFormat('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  }).format(new Date(date));
}
