import { useEffect, useRef, useState } from 'react';
import {
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from '@tanstack/react-query';
import { api, type PaperDoc } from '../../hooks/api';
import { PaperEditor } from './PaperEditor';

const PAGE_SIZE = 60;

export function Paper() {
  const queryClient = useQueryClient();
  const [openId, setOpenId] = useState<string | null>(null);
  const [deleteMode, setDeleteMode] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const sentinelRef = useRef<HTMLDivElement>(null);

  const { data, fetchNextPage, hasNextPage, isFetchingNextPage, isLoading } =
    useInfiniteQuery({
      queryKey: ['paper'],
      queryFn: ({ pageParam }) =>
        api.paper.list({ limit: PAGE_SIZE, offset: pageParam }),
      initialPageParam: 0,
      getNextPageParam: (lastPage, allPages) =>
        lastPage.length < PAGE_SIZE ? undefined : allPages.length * PAGE_SIZE,
    });

  const papers = data?.pages.flat() ?? [];

  const create = useMutation({
    mutationFn: () => api.paper.create(),
    onSuccess: ({ id }) => {
      queryClient.invalidateQueries({ queryKey: ['paper'] });
      setOpenId(id);
    },
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.paper.remove(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['paper'] }),
  });

  useEffect(() => {
    if (typeof IntersectionObserver === 'undefined') return;
    const sentinel = sentinelRef.current;
    const root = scrollRef.current;
    if (!sentinel || !root) return;
    const io = new IntersectionObserver(
      ([e]) => {
        if (e.isIntersecting && hasNextPage && !isFetchingNextPage) {
          fetchNextPage();
        }
      },
      { root, rootMargin: '400px' }
    );
    io.observe(sentinel);
    return () => io.disconnect();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);

  if (openId) {
    return <PaperEditor paperId={openId} onBack={() => setOpenId(null)} />;
  }

  return (
    <div className="flex-1 flex flex-col p-4 overflow-hidden">
      <div className="flex items-center justify-end mb-4">
        <button
          onClick={() => setDeleteMode(m => !m)}
          className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
            deleteMode
              ? 'bg-red-600 text-white'
              : 'bg-[var(--color-surface)] hover:bg-white/10'
          }`}
          title={deleteMode ? 'Finish deleting' : 'Delete papers'}
        >
          {deleteMode ? 'Done' : '🗑 Delete'}
        </button>
      </div>
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4">
          {/* Create new paper — hidden while deleting to keep the two modes distinct. */}
          {!deleteMode && (
            <button
              onClick={() => create.mutate()}
              disabled={create.isPending}
              className="aspect-[210/297] rounded-lg border-2 border-dashed border-white/15 flex flex-col items-center justify-center gap-2 transition-colors hover:bg-white/5 disabled:opacity-50"
            >
              <span className="text-4xl leading-none">＋</span>
              <span className="text-sm opacity-70">New paper</span>
            </button>
          )}

          {papers.map((p: PaperDoc) => (
            <div key={p.id} className="relative">
              <button
                onClick={() =>
                  deleteMode ? remove.mutate(p.id) : setOpenId(p.id)
                }
                /* A4, like the page itself — the snapshot is an A4 sheet, and a
                   3:4 tile cropped the bottom off every thumbnail. */
                className={`w-full aspect-[210/297] rounded-lg overflow-hidden border bg-white shadow-sm transition-shadow ${
                  deleteMode
                    ? 'border-red-500 hover:shadow-md'
                    : 'border-white/10 hover:shadow-md'
                }`}
              >
                {p.firstPageImageUrl ? (
                  <img
                    src={p.firstPageImageUrl}
                    alt={p.title || 'Paper'}
                    className="w-full h-full object-cover object-top"
                  />
                ) : (
                  <div className="w-full h-full flex items-center justify-center text-neutral-300 text-3xl">
                    ✎
                  </div>
                )}
              </button>
              {deleteMode && (
                <div className="absolute inset-0 rounded-lg bg-red-600/30 flex items-center justify-center pointer-events-none">
                  <span className="w-9 h-9 rounded-full bg-red-600 text-white flex items-center justify-center text-lg shadow">
                    🗑
                  </span>
                </div>
              )}
              {p.pageCount > 1 && (
                <span className="absolute bottom-1 right-1 px-1.5 py-0.5 rounded bg-black/60 text-white text-[10px]">
                  {p.pageCount}
                </span>
              )}
              {p.pendingArchive && !deleteMode && (
                <span
                  className="absolute top-1 left-1 px-1.5 py-0.5 rounded bg-[var(--color-primary)] text-[var(--color-bg)] text-[10px] font-medium"
                  title="Moves to the Journal at 4am"
                >
                  📓 Journal
                </span>
              )}
            </div>
          ))}
        </div>

        {isLoading && (
          <div className="py-8 text-center text-neutral-500">Loading…</div>
        )}
        <div ref={sentinelRef} className="h-1" />
      </div>
    </div>
  );
}
