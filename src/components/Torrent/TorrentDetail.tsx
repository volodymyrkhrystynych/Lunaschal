import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/hooks/api';
import {
  daysUntilPurge,
  formatBytes,
  formatPercent,
  stateColor,
  stateLabel,
  type Torrent,
} from '@/lib/torrents';

/**
 * One torrent: its files, its limits, and the two things Lunaschal owns about
 * it (the note and the retention policy).
 *
 * The split matters — limits and category are written straight to qBittorrent
 * and read back from it, while the note and retention live in our row. Nothing
 * is stored in both places, so nothing can drift.
 */
export function TorrentDetail({
  torrent,
  categories,
}: {
  torrent: Torrent;
  categories: string[];
}) {
  const queryClient = useQueryClient();
  const [note, setNote] = useState(torrent.note ?? '');
  const [retention, setRetention] = useState(
    String(torrent.retentionDays ?? '')
  );
  const [ratio, setRatio] = useState(String(torrent.ratioLimit ?? ''));
  const [category, setCategory] = useState(torrent.category);
  // Shown in KiB/s; qBittorrent takes bytes/s, and 0 there is unlimited.
  const [dlLimit, setDlLimit] = useState(
    torrent.dlLimit ? String(Math.round(torrent.dlLimit / 1024)) : ''
  );
  const [upLimit, setUpLimit] = useState(
    torrent.upLimit ? String(Math.round(torrent.upLimit / 1024)) : ''
  );

  // Re-seed the fields when the selection moves; otherwise the previous
  // torrent's note is shown over the new one's.
  //
  // Keyed on infoHash alone, deliberately. Depending on the field values too
  // would re-run this on every 1.5s list poll and overwrite whatever was being
  // typed mid-word.
  useEffect(() => {
    setNote(torrent.note ?? '');
    setRetention(String(torrent.retentionDays ?? ''));
    setRatio(String(torrent.ratioLimit ?? ''));
    setCategory(torrent.category);
    setDlLimit(
      torrent.dlLimit ? String(Math.round(torrent.dlLimit / 1024)) : ''
    );
    setUpLimit(
      torrent.upLimit ? String(Math.round(torrent.upLimit / 1024)) : ''
    );
  }, [torrent.infoHash]);

  const { data: files } = useQuery({
    queryKey: ['torrent-files', torrent.infoHash],
    queryFn: () => api.torrents.files(torrent.infoHash),
    // Cheap, and file progress moves while the torrent does.
    refetchInterval: torrent.live ? 3000 : false,
  });

  const save = useMutation({
    mutationFn: () =>
      api.torrents.update(torrent.infoHash, {
        note,
        retentionDays: retention === '' ? null : Number(retention),
        ratioLimit: ratio === '' ? null : Number(ratio),
        category,
        // Blank means unlimited, which qBittorrent spells 0.
        dlLimit: dlLimit === '' ? 0 : Number(dlLimit) * 1024,
        upLimit: upLimit === '' ? 0 : Number(upLimit) * 1024,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['torrents'] }),
  });

  const purgeIn = daysUntilPurge(torrent, Date.now());

  return (
    <div className="space-y-4 text-sm">
      <div>
        <div className="font-medium break-words">{torrent.name}</div>
        <div className={`text-xs ${stateColor(torrent.stateGroup)}`}>
          {stateLabel(torrent.stateGroup)} · {formatPercent(torrent.progress)}{' '}
          of {formatBytes(torrent.size)} · ratio {torrent.ratio.toFixed(2)}
        </div>
        {!torrent.tracked && (
          <div className="text-xs text-[var(--color-text-muted)] mt-1">
            Added outside Lunaschal — saving a note here starts tracking it.
          </div>
        )}
      </div>

      <div className="grid grid-cols-2 gap-2">
        <label className="flex flex-col gap-1">
          <span className="text-xs text-[var(--color-text-muted)]">
            Stop seeding at ratio
          </span>
          <input
            value={ratio}
            onChange={e => setRatio(e.target.value)}
            placeholder="global"
            className="rounded bg-[var(--color-bg)] border border-white/10 px-2 py-1"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs text-[var(--color-text-muted)]">
            Delete after (days, 0 = keep)
          </span>
          <input
            type="number"
            min={0}
            value={retention}
            onChange={e => setRetention(e.target.value)}
            className="rounded bg-[var(--color-bg)] border border-white/10 px-2 py-1"
          />
        </label>
      </div>

      {purgeIn !== null && (
        <div className="text-xs text-yellow-400">
          Scheduled for deletion in {purgeIn} day{purgeIn === 1 ? '' : 's'},
          files included.
        </div>
      )}

      <div className="grid grid-cols-2 gap-2">
        <label className="flex flex-col gap-1">
          <span className="text-xs text-[var(--color-text-muted)]">
            Download limit (KiB/s, blank = unlimited)
          </span>
          <input
            type="number"
            min={0}
            value={dlLimit}
            onChange={e => setDlLimit(e.target.value)}
            className="rounded bg-[var(--color-bg)] border border-white/10 px-2 py-1"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs text-[var(--color-text-muted)]">
            Upload limit (KiB/s, blank = unlimited)
          </span>
          <input
            type="number"
            min={0}
            value={upLimit}
            onChange={e => setUpLimit(e.target.value)}
            className="rounded bg-[var(--color-bg)] border border-white/10 px-2 py-1"
          />
        </label>
      </div>

      <label className="flex flex-col gap-1">
        <span className="text-xs text-[var(--color-text-muted)]">Category</span>
        <input
          list="torrent-detail-categories"
          value={category}
          onChange={e => setCategory(e.target.value)}
          placeholder="none"
          className="rounded bg-[var(--color-bg)] border border-white/10 px-2 py-1"
        />
        <datalist id="torrent-detail-categories">
          {categories.map(c => (
            <option key={c} value={c} />
          ))}
        </datalist>
      </label>

      <label className="flex flex-col gap-1">
        <span className="text-xs text-[var(--color-text-muted)]">Note</span>
        <textarea
          value={note}
          onChange={e => setNote(e.target.value)}
          rows={2}
          className="rounded bg-[var(--color-bg)] border border-white/10 px-2 py-1"
        />
      </label>

      <button
        onClick={() => save.mutate()}
        disabled={save.isPending}
        className="px-3 py-1.5 rounded bg-[var(--color-primary)] text-black text-sm font-medium disabled:opacity-40"
      >
        {save.isPending ? 'Saving…' : 'Save'}
      </button>
      {save.isError && (
        <div className="text-sm text-red-400">
          {(save.error as Error).message}
        </div>
      )}

      <div>
        <div className="text-xs text-[var(--color-text-muted)] mb-1">Files</div>
        <ul className="space-y-1">
          {(files ?? []).map(f => (
            <li
              key={f.index}
              className="flex items-center justify-between gap-2"
            >
              <span className="truncate" title={f.name}>
                {f.name}
              </span>
              <span className="shrink-0 text-xs text-[var(--color-text-muted)]">
                {formatBytes(f.size)} · {formatPercent(f.progress)}
              </span>
              {f.progress >= 1 && (
                // A plain link, not a fetch: this is what gives the browser
                // range requests, so a video streams and seeks over Tailscale
                // instead of having to download whole first.
                <a
                  href={api.torrents.fileUrl(torrent.infoHash, f.index)}
                  className="shrink-0 text-xs text-[var(--color-primary)] underline"
                >
                  open
                </a>
              )}
            </li>
          ))}
          {files?.length === 0 && (
            <li className="text-xs text-[var(--color-text-muted)]">
              No files yet — still fetching metadata.
            </li>
          )}
        </ul>
      </div>
    </div>
  );
}
