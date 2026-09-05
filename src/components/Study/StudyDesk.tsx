import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import type { StudySource } from '../../lib/study';
import { SourceViewer } from './SourceViewer';
import { StudyNotePane } from './StudyNotePane';

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
          <SourceViewer source={source} />
        </div>
        <div className="w-px bg-white/10 shrink-0" />
        <div className="flex-1 flex flex-col overflow-hidden">
          <StudyNotePane source={source} />
        </div>
      </div>
    </div>
  );
}
