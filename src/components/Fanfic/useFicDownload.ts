import { useCallback, useEffect, useMemo, useState } from 'react';
import { onlineManager, useQueryClient } from '@tanstack/react-query';
import { api, type FicChapterSummary } from '../../hooks/api';

// Chapter content is normally cached only when you open a chapter (see Reader).
// This bulk-fetches every chapter's content query so the whole fic is readable
// offline: fetched queries land in the persisted IndexedDB cache like any other.
// A few in parallel — these are our own backend, not an external forum.
const CONCURRENCY = 4;

export type FicDownloadStatus = 'idle' | 'downloading' | 'done' | 'error';

export interface FicDownloadState {
  status: FicDownloadStatus;
  /** How many chapters' content is currently cached. */
  done: number;
  total: number;
  start: () => void;
}

const chapterKey = (id: string) => ['fanfic', 'chapter', id] as const;

export function useFicDownload(
  chapters: FicChapterSummary[] | undefined
): FicDownloadState {
  const qc = useQueryClient();
  const list = useMemo(() => chapters ?? [], [chapters]);
  const total = list.length;

  const isCached = useCallback(
    (id: string) => qc.getQueryData(chapterKey(id)) !== undefined,
    [qc]
  );
  const countCached = useCallback(
    () => list.reduce((n, ch) => (isCached(ch.id) ? n + 1 : n), 0),
    [list, isCached]
  );

  const [status, setStatus] = useState<FicDownloadStatus>('idle');
  const [done, setDone] = useState(() => countCached());

  // Keep `done` in sync as chapters load through normal reading, or when the
  // fic (and thus its cached set) changes.
  useEffect(() => setDone(countCached()), [countCached]);

  const start = useCallback(async () => {
    if (total === 0) return;
    // Saving pulls every chapter from the backend — impossible offline. Bail
    // immediately rather than spawning workers that await paused fetches forever
    // (which is what stranded the progress bar partway through).
    if (!onlineManager.isOnline()) {
      setStatus('error');
      return;
    }
    setStatus('downloading');
    setDone(countCached());

    const pending = list.filter(ch => !isCached(ch.id));
    let failed = false;
    let next = 0;

    const worker = async () => {
      while (next < pending.length) {
        const ch = pending[next++];
        try {
          await qc.fetchQuery({
            queryKey: chapterKey(ch.id),
            queryFn: () => api.fanfic.chapter(ch.id),
            staleTime: Infinity, // already-cached chapters are reused, not refetched
            // Never let a mid-download disconnect leave a worker hanging on a
            // paused fetch; surface it as a failure so status settles.
            networkMode: 'always',
          });
        } catch {
          failed = true;
        }
        setDone(countCached());
      }
    };

    await Promise.all(
      Array.from({ length: Math.min(CONCURRENCY, pending.length) }, worker)
    );
    setStatus(failed ? 'error' : 'done');
  }, [qc, list, total, isCached, countCached]);

  return { status, done, total, start };
}
