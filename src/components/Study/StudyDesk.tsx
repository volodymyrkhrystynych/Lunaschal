import { useCallback, useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import {
  isSourceRowFor,
  type NoteMode,
  type StudySource,
} from '../../lib/study';
import type { PaperEditorHandle } from '../Paper/PaperEditor';
import { SourceViewer } from './SourceViewer';
import { StudyNotePane } from './StudyNotePane';
import { StudyPaperPane } from './StudyPaperPane';

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
  const queryClient = useQueryClient();
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

  // Which pane the right half shows. Held locally as well as on the row so the
  // toggle is instant: the PATCH is the *memory* of the choice, not the choice
  // itself, and it is deliberately not invalidating anything (see below).
  const [mode, setMode] = useState<NoteMode | null>(null);
  const noteMode = mode ?? source?.noteMode ?? 'note';
  useEffect(() => setMode(null), [sourceId]);

  // The paper pane's way of writing the open page to the device. Leaving the
  // desk unmounts it, and a passive cleanup runs after React has already
  // detached the canvas — so the commit has to happen here, before we stop
  // rendering it, or the last strokes are lost.
  const paperRef = useRef<PaperEditorHandle | null>(null);

  const switchMode = useCallback(
    (next: NoteMode) => {
      // Switching away from the paper unmounts it just as leaving the desk
      // does, so it gets the same commit first.
      if (next !== 'paper') void paperRef.current?.commitLocal();
      setMode(next);
      // Same fire-and-forget lane as the position write: which pane you had
      // open is a preference, and the server does not bump `updated_at` for it.
      void api.study
        .update(sourceId, { noteMode: next })
        .then(updated => {
          if (!isSourceRowFor(updated, sourceId)) return;
          queryClient.setQueryData(['study', 'source', sourceId], updated);
        })
        .catch(() => {});
    },
    [sourceId, queryClient]
  );

  // Filing the source into the Journal. Unlike the position and mode writes
  // below, this is a real edit the rest of the app reads, so it invalidates:
  // the library must drop a source that has moved, and the feed must gain it.
  const setArchive = useMutation({
    mutationFn: (archiveRequested: boolean) =>
      api.study.update(sourceId, { archiveRequested }),
    onSuccess: updated => {
      if (isSourceRowFor(updated, sourceId)) {
        queryClient.setQueryData(['study', 'source', sourceId], updated);
      }
      queryClient.invalidateQueries({ queryKey: ['study', 'sources'] });
      queryClient.invalidateQueries({ queryKey: ['study', 'journal'] });
    },
  });

  const leave = useCallback(() => {
    // Fired, not awaited: the commit is local-only (two IndexedDB writes and a
    // canvas snapshot), and Back must not sit waiting on it.
    void paperRef.current?.commitLocal();
    onBack();
  }, [onBack]);

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

  // `!source.id`, not just `!source`. `initialData` and the persisted cache
  // both hand this query a row without going near the network, so what arrives
  // here is not guaranteed to be a source at all — and every pane below treats
  // whatever it is given as one, reading `notePath` and `paperId` to decide
  // whether to *create* a note or a paper. A row of nulls therefore reads as a
  // source that has neither, and the panes make a second, orphaned copy of
  // both. An id is the one field a real row always has, so it is the thing to
  // check; without it this is still loading.
  if (!source?.id) {
    return (
      <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)]">
        Loading…
      </div>
    );
  }

  // `?? false`, for the same reason `isSourceRowFor` exists: what this query
  // hands back can be a row persisted before the field existed.
  const filed = source.pendingArchive ?? false;

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="shrink-0 flex items-center gap-2 px-3 py-1.5 border-b border-white/10 bg-[var(--color-surface)]">
        <button
          type="button"
          onClick={leave}
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
        <div className="ml-auto flex items-center gap-1 shrink-0">
          <ModeButton
            active={filed}
            onClick={() => setArchive.mutate(!filed)}
            title={
              filed
                ? 'Filed for the Journal — moves at 4am. Tap to keep it here.'
                : 'File this into the Journal (moves at 4am), with its notes and pages'
            }
          >
            📓 {filed ? 'To journal ✓' : 'To journal'}
          </ModeButton>
          <span className="w-px h-4 bg-white/10 mx-1" />
          <ModeButton
            active={noteMode === 'note'}
            onClick={() => switchMode('note')}
          >
            ⌨ Notes
          </ModeButton>
          <ModeButton
            active={noteMode === 'paper'}
            onClick={() => switchMode('paper')}
          >
            ✎ Paper
          </ModeButton>
        </div>
      </div>
      <div className="flex-1 flex overflow-hidden">
        <div className="w-1/2 flex flex-col overflow-hidden">
          <SourceViewer source={source} onPosition={savePosition} />
        </div>
        <div className="w-px bg-white/10 shrink-0" />
        <div className="flex-1 flex flex-col overflow-hidden">
          {noteMode === 'paper' ? (
            <StudyPaperPane source={source} handleRef={paperRef} />
          ) : (
            <StudyNotePane source={source} />
          )}
        </div>
      </div>
    </div>
  );
}

function ModeButton({
  active,
  onClick,
  children,
  title,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-pressed={active}
      className={
        active
          ? 'px-2 py-0.5 rounded text-xs bg-[var(--color-primary)] text-[var(--color-bg)]'
          : 'px-2 py-0.5 rounded text-xs hover:bg-white/10 text-[var(--color-text-muted)]'
      }
    >
      {children}
    </button>
  );
}
