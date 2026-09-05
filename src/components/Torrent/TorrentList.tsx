import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/hooks/api';
import { useShortcutScope } from '@/shortcuts/ShortcutProvider';
import { useListSelection } from '@/shortcuts/useListSelection';
import {
  anyLive,
  filterTorrents,
  formatBytes,
  formatEta,
  formatPercent,
  formatSpeed,
  sortTorrents,
  stateColor,
  stateLabel,
  SORTS,
  type TorrentSort,
} from '@/lib/torrents';
import { AddTorrent } from './AddTorrent';
import { TorrentDetail } from './TorrentDetail';
import { VpnBanner } from './VpnBanner';

export function Torrent() {
  const queryClient = useQueryClient();
  const [showAdd, setShowAdd] = useState(false);
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('');
  const [sort, setSort] = useState<TorrentSort>('added');
  const searchRef = useRef<HTMLInputElement>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ['torrents'],
    queryFn: api.torrents.list,
    // Only while something is actually moving. A finished library polling
    // forever is the thing this avoids.
    refetchInterval: query =>
      anyLive(query.state.data?.torrents ?? []) ? 1500 : false,
  });

  const { data: categories } = useQuery({
    queryKey: ['torrent-categories'],
    queryFn: api.torrents.categories,
    // The list endpoint failing already tells the user the stack is down.
    retry: false,
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['torrents'] });
    queryClient.invalidateQueries({ queryKey: ['torrent-status'] });
  };

  const act = useMutation({
    mutationFn: ({
      hash,
      action,
    }: {
      hash: string;
      action: 'pause' | 'resume' | 'recheck';
    }) => api.torrents[action](hash),
    onSuccess: invalidate,
  });

  const remove = useMutation({
    mutationFn: ({
      hash,
      deleteFiles,
    }: {
      hash: string;
      deleteFiles: boolean;
    }) => api.torrents.remove(hash, deleteFiles),
    onSuccess: invalidate,
  });

  const all = data?.torrents ?? [];
  const visible = sortTorrents(filterTorrents(all, { search, category }), sort);

  const {
    selIndex,
    setSelIndex,
    next,
    prev,
    isSelected,
    scrollSelectedIntoView,
  } = useListSelection(visible.length, 1);

  useShortcutScope(1, {
    next,
    prev,
    create: () => setShowAdd(true),
    search: () => {
      searchRef.current?.focus();
      searchRef.current?.select();
    },
    drillIn: () => {
      const torrent = visible[selIndex];
      if (!torrent) return false;
      act.mutate({
        hash: torrent.infoHash,
        action: torrent.stateGroup === 'paused' ? 'resume' : 'pause',
      });
      return true;
    },
  });

  const selected = visible[selIndex];

  return (
    <div className="flex-1 flex flex-col p-4 overflow-hidden gap-3">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-lg font-semibold text-[var(--color-text)]">
          Torrents
        </h2>
        <button
          onClick={() => setShowAdd(v => !v)}
          className="px-3 py-1.5 rounded bg-[var(--color-primary)] text-black text-sm font-medium"
        >
          {showAdd ? 'Close' : 'Add'}
        </button>
      </div>

      <VpnBanner vpn={data?.vpn} />

      {showAdd && (
        <AddTorrent
          categories={categories ?? []}
          onClose={() => setShowAdd(false)}
        />
      )}

      <div className="flex flex-wrap gap-2 text-sm">
        <input
          ref={searchRef}
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Filter by name"
          className="rounded bg-[var(--color-surface)] border border-white/10 px-2 py-1 flex-1 min-w-[10rem]"
        />
        <select
          value={category}
          onChange={e => setCategory(e.target.value)}
          className="rounded bg-[var(--color-surface)] border border-white/10 px-2 py-1"
        >
          <option value="">All categories</option>
          {(categories ?? []).map(c => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <select
          value={sort}
          onChange={e => setSort(e.target.value as TorrentSort)}
          className="rounded bg-[var(--color-surface)] border border-white/10 px-2 py-1"
        >
          {SORTS.map(s => (
            <option key={s} value={s}>
              Sort: {s}
            </option>
          ))}
        </select>
      </div>

      {error && (
        <div className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-400">
          {(error as Error).message}
        </div>
      )}
      {isLoading && (
        <div className="text-sm text-[var(--color-text-muted)]">Loading…</div>
      )}
      {!isLoading && !error && all.length === 0 && (
        <div className="text-sm text-[var(--color-text-muted)]">
          Nothing downloading. Paste a magnet link to start.
        </div>
      )}

      <div className="flex-1 flex gap-4 overflow-hidden">
        <ul className="flex-1 overflow-y-auto space-y-1">
          {visible.map((t, i) => (
            <li
              key={t.infoHash}
              ref={scrollSelectedIntoView(i)}
              onClick={() => setSelIndex(i)}
              className={`rounded border px-3 py-2 cursor-pointer ${
                isSelected(i)
                  ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/10'
                  : 'border-white/10 hover:bg-white/5'
              }`}
            >
              <div className="flex items-baseline justify-between gap-2">
                <span className="truncate text-sm" title={t.name}>
                  {t.name}
                </span>
                <span
                  className={`shrink-0 text-xs ${stateColor(t.stateGroup)}`}
                >
                  {stateLabel(t.stateGroup)}
                </span>
              </div>
              <div className="mt-1 h-1 rounded bg-white/10 overflow-hidden">
                <div
                  className="h-full bg-[var(--color-primary)]"
                  style={{ width: formatPercent(t.progress) }}
                />
              </div>
              <div className="mt-1 flex flex-wrap gap-x-3 text-xs text-[var(--color-text-muted)]">
                <span>{formatPercent(t.progress)}</span>
                <span>{formatBytes(t.size)}</span>
                <span>↓ {formatSpeed(t.dlSpeed)}</span>
                <span>↑ {formatSpeed(t.upSpeed)}</span>
                <span>ETA {formatEta(t.eta)}</span>
                <span>
                  {t.numSeeds}/{t.numLeechs} peers
                </span>
                {t.category && <span>#{t.category}</span>}
              </div>
              <div className="mt-1 flex gap-2 text-xs">
                <button
                  onClick={e => {
                    e.stopPropagation();
                    act.mutate({
                      hash: t.infoHash,
                      action: t.stateGroup === 'paused' ? 'resume' : 'pause',
                    });
                  }}
                  className="px-2 py-0.5 rounded border border-white/10"
                >
                  {t.stateGroup === 'paused' ? 'Resume' : 'Pause'}
                </button>
                <button
                  onClick={e => {
                    e.stopPropagation();
                    remove.mutate({ hash: t.infoHash, deleteFiles: false });
                  }}
                  className="px-2 py-0.5 rounded border border-white/10"
                >
                  Remove
                </button>
                <button
                  onClick={e => {
                    e.stopPropagation();
                    // Irreversible and off by default everywhere else in this
                    // feature, so it asks.
                    if (
                      window.confirm(
                        `Delete "${t.name}" and its downloaded files?`
                      )
                    ) {
                      remove.mutate({ hash: t.infoHash, deleteFiles: true });
                    }
                  }}
                  className="px-2 py-0.5 rounded border border-red-500/40 text-red-400"
                >
                  Delete + files
                </button>
              </div>
            </li>
          ))}
        </ul>

        {selected && (
          <aside className="w-80 shrink-0 overflow-y-auto border-l border-white/10 pl-4 hidden md:block">
            <TorrentDetail torrent={selected} categories={categories ?? []} />
          </aside>
        )}
      </div>
    </div>
  );
}
