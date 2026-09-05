import { useCallback, useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import type { StudySource } from '../../lib/study';
import { SourceViewer } from './SourceViewer';
import { StudyNotePane } from './StudyNotePane';

// Long enough that a flick through twenty pages is one write, short enough
// that closing the tab a moment later still lands. NotebookEditorPane's save
// debounce is the same hand-rolled shape — the repo has no helper for this.
const POSITION_SAVE_DEBOUNCE_MS = 1500;

interface Props {
  sourceId: string;
  initial?: StudySource;
  onBack: () => void;
}

/**
 * The desk: source on the left, notes on the right, split down the middle.
 *
 * The divider is fixed at half — there is no resizer anywhere in the app yet,
 * and a draggable one is a separate piece of work (the drag math would belong
 * in src/lib/ beside paperImages.ts, not in here).
 */
export function StudyDesk({ sourceId, initial, onBack }: Props) {
  const { data: source } = useQuery({
    queryKey: ['study', 'source', sourceId],
    queryFn: () => api.study.source(sourceId),
    initialData: initial,
    // While an import is still running the row is genuinely changing under us,
    // so poll — and stop the moment it lands.
    refetchInterval: q =>
      q.state.data?.importStatus === 'importing' ? 2000 : false,
  });

  // Records that this source was opened, which is what a "recently studied"
  // ordering would read. Fired once per open, not on every refetch.
  useEffect(() => {
    void api.study.update(sourceId, { touch: true }).catch(() => {});
  }, [sourceId]);

  // Where the reader has got to, written on a debounce.
  //
  // Deliberately the bare fire-and-forget lane the `touch` write above uses:
  // no useMutation, no offline queue, and **no query invalidation**. This
  // fires every few seconds of scrolling or playback, and invalidating would
  // refetch the very row being read — `fanficProgressCfg` can afford two
  // invalidations per write only because it fires once per chapter. Losing the
  // last few seconds of a position to a closed laptop costs nothing.
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingRef = useRef<number | null>(null);

  const savePosition = useCallback(
    (position: number) => {
      pendingRef.current = position;
      if (timerRef.current) return;
      timerRef.current = setTimeout(() => {
        timerRef.current = null;
        const value = pendingRef.current;
        pendingRef.current = null;
        if (value !== null) {
          void api.study.update(sourceId, { position: value }).catch(() => {});
        }
      }, POSITION_SAVE_DEBOUNCE_MS);
    },
    [sourceId]
  );

  // Leaving the desk is the one moment the exact position matters, so the
  // pending write is flushed rather than dropped with the timer.
  useEffect(
    () => () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = null;
      const value = pendingRef.current;
      pendingRef.current = null;
      if (value !== null) {
        void api.study.update(sourceId, { position: value }).catch(() => {});
      }
    },
    [sourceId]
  );

  if (!source) {
    return (
      <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)]">
        Loading…
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="shrink-0 flex items-center gap-2 px-3 py-1.5 border-b border-white/10 bg-[var(--color-surface)]">
        <button
          type="button"
          onClick={onBack}
          className="px-2 py-0.5 rounded hover:bg-white/10 text-[var(--color-text)]"
        >
          ‹ Sources
        </button>
        <span className="font-medium text-[var(--color-text)] truncate">
          {source.title || 'Untitled'}
        </span>
        {source.sourceUrl && (
          <a
            href={source.sourceUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-[var(--color-text-muted)] hover:underline truncate"
          >
            {source.sourceUrl}
          </a>
        )}
      </div>
      <div className="flex-1 flex overflow-hidden">
        <div className="w-1/2 flex flex-col overflow-hidden">
          <SourceViewer source={source} onPosition={savePosition} />
        </div>
        <div className="w-px bg-white/10 shrink-0" />
        <div className="flex-1 flex flex-col overflow-hidden">
          <StudyNotePane source={source} />
        </div>
      </div>
    </div>
  );
}
