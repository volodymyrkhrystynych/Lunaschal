import { useEffect, useRef } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type KnowledgeDownload } from '../../hooks/api';
import {
  actionsFor,
  etaLabel,
  isActive,
  percent,
  progressLabel,
  rateLabel,
  statusLabel,
} from '../../lib/knowledgeDownloads';

function DownloadRow({ download }: { download: KnowledgeDownload }) {
  const client = useQueryClient();
  const invalidate = () => {
    void client.invalidateQueries({ queryKey: ['knowledge'] });
  };
  const pause = useMutation({
    mutationFn: () => api.knowledge.pauseDownload(download.id),
    onSuccess: invalidate,
  });
  const resume = useMutation({
    mutationFn: () => api.knowledge.resumeDownload(download.id),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: () => api.knowledge.deleteDownload(download.id),
    onSuccess: invalidate,
  });

  const done = percent(download);
  const actions = actionsFor(download);
  const rate = rateLabel(download.bytesPerSecond);
  const eta = etaLabel(download);

  return (
    <div className="rounded border border-white/10 p-2 bg-[var(--color-surface)]">
      <div className="flex items-baseline gap-2">
        <div className="text-xs font-medium truncate">
          {download.title || download.filename}
        </div>
        <div className="ml-auto shrink-0 text-[11px] text-[var(--color-text-muted)]">
          {statusLabel(download.status)}
        </div>
      </div>

      {done !== null && (
        <div className="mt-1 h-1 rounded bg-white/10 overflow-hidden">
          <div
            className={`h-full ${download.status === 'error' ? 'bg-red-400' : 'bg-[var(--color-primary)]'}`}
            style={{ width: `${done}%` }}
            role="progressbar"
            aria-label={`${download.filename} progress`}
            aria-valuenow={done}
            aria-valuemin={0}
            aria-valuemax={100}
          />
        </div>
      )}

      <div className="mt-1 text-[11px] text-[var(--color-text-muted)] flex flex-wrap gap-x-2">
        <span>{progressLabel(download)}</span>
        {rate && <span>{rate}</span>}
        {eta && <span>{eta}</span>}
      </div>

      {download.error && (
        <p className="mt-1 text-[11px] text-red-400">{download.error}</p>
      )}

      <div className="mt-1 flex gap-2 text-[11px]">
        {actions.pause && (
          <button
            type="button"
            onClick={() => pause.mutate()}
            className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
          >
            Pause
          </button>
        )}
        {actions.resume && (
          <button
            type="button"
            onClick={() => resume.mutate()}
            className="text-[var(--color-primary)]"
          >
            Resume
          </button>
        )}
        {actions.remove && (
          <button
            type="button"
            onClick={() => remove.mutate()}
            className="ml-auto text-[var(--color-text-muted)] hover:text-red-400"
          >
            Remove
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * The active-downloads strip at the foot of the sidebar.
 *
 * Polls while anything is in flight and stops when nothing is: the byte count
 * in the row is only checkpointed every 16 MiB, so the server merges the live
 * counter over it and this has to ask often enough for that to be worth
 * having. Finished downloads drop out of the strip entirely — the archive
 * itself appears in the list above, which is the better place to see it.
 */
export function DownloadStrip() {
  const client = useQueryClient();
  const completed = useRef(new Set<string>());
  const downloads = useQuery({
    queryKey: ['knowledge', 'downloads'],
    queryFn: api.knowledge.downloads,
    refetchInterval: query =>
      (query.state.data ?? []).some(isActive) ? 1000 : false,
  });

  useEffect(() => {
    const done = (downloads.data ?? []).filter(item => item.status === 'done');
    if (done.some(item => !completed.current.has(item.id))) {
      void client.invalidateQueries({ queryKey: ['knowledge', 'archives'] });
    }
    for (const item of done) completed.current.add(item.id);
  }, [downloads.data, client]);

  const active = (downloads.data ?? []).filter(isActive);
  if (!active.length) return null;

  return (
    <section className="mt-3 pt-3 border-t border-white/10">
      <h3 className="text-xs uppercase tracking-wide text-[var(--color-text-muted)] mb-1">
        Downloads
        <span className="ml-1 opacity-70">({active.length})</span>
      </h3>
      <div className="space-y-2">
        {active.map(download => (
          <DownloadRow key={download.id} download={download} />
        ))}
      </div>
    </section>
  );
}
