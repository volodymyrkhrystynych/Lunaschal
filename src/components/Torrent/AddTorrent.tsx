import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/hooks/api';

interface Props {
  categories: string[];
  onClose: () => void;
}

/**
 * Paste seeds. A textarea rather than a single input because pasting a batch of
 * magnets is the normal case, and the backend reports per-link errors so one bad
 * line does not discard the rest — that is surfaced here rather than swallowed.
 */
export function AddTorrent({ categories, onClose }: Props) {
  const queryClient = useQueryClient();
  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.settings.get,
  });
  const [magnets, setMagnets] = useState('');
  const [category, setCategory] = useState('');
  const [note, setNote] = useState('');
  // Blank until the settings arrive, then pre-filled with the configured
  // default so this box agrees with Settings → Torrents. `retentionTouched`
  // stops a late-arriving settings response from overwriting a typed value.
  const [retention, setRetention] = useState('');
  const [retentionTouched, setRetentionTouched] = useState(false);
  const [errors, setErrors] = useState<{ input: string; error: string }[]>([]);
  const [files, setFiles] = useState<File[]>([]);

  useEffect(() => {
    if (retentionTouched || !settings) return;
    setRetention(String(settings.torrentDefaultRetentionDays || ''));
  }, [settings, retentionTouched]);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['torrents'] });
    queryClient.invalidateQueries({ queryKey: ['torrent-status'] });
  };

  const add = useMutation({
    mutationFn: async () => {
      const shared = {
        category: category || undefined,
        note: note || undefined,
        // 0 rather than null for "keep forever": both mean that in the
        // schema, and 0 survives the multipart path, where a null would be
        // dropped and silently fall back to the configured default instead.
        retentionDays: retention === '' ? 0 : Number(retention),
      };
      const results = [];
      if (magnets.trim())
        results.push(await api.torrents.add({ magnets, ...shared }));
      if (files.length)
        results.push(await api.torrents.addFiles(files, shared));
      return results;
    },
    onSuccess: results => {
      invalidate();
      const failed = results.flatMap(r => r.errors);
      setErrors(failed);
      // Keep the box open when something was rejected, so the message is
      // readable next to the input that caused it.
      if (!failed.length) onClose();
      else setMagnets('');
    },
  });

  const nothingToAdd = !magnets.trim() && !files.length;

  return (
    <div className="rounded border border-white/10 bg-[var(--color-surface)] p-3 space-y-3">
      <textarea
        autoFocus
        value={magnets}
        onChange={e => setMagnets(e.target.value)}
        rows={4}
        placeholder={
          'magnet:?xt=urn:btih:…\nOne per line — paste as many as you like.'
        }
        className="w-full rounded bg-[var(--color-bg)] border border-white/10 px-2 py-1.5 text-sm font-mono"
      />

      <div className="flex flex-wrap gap-2 items-center text-sm">
        {/* iPad can gray out .torrent files with an accept filter. The backend
            validates the uploaded contents instead of trusting the file type. */}
        <input
          aria-label="Torrent files (.torrent)"
          type="file"
          multiple
          onChange={e => setFiles(Array.from(e.target.files ?? []))}
          className="text-xs text-[var(--color-text-muted)] max-w-[16rem]"
        />
        <input
          list="torrent-categories"
          value={category}
          onChange={e => setCategory(e.target.value)}
          placeholder="Category"
          className="rounded bg-[var(--color-bg)] border border-white/10 px-2 py-1 w-32"
        />
        <datalist id="torrent-categories">
          {categories.map(c => (
            <option key={c} value={c} />
          ))}
        </datalist>
        <label className="flex items-center gap-1 text-[var(--color-text-muted)]">
          Delete after
          <input
            type="number"
            min={0}
            value={retention}
            onChange={e => {
              setRetentionTouched(true);
              setRetention(e.target.value);
            }}
            className="rounded bg-[var(--color-bg)] border border-white/10 px-2 py-1 w-16"
          />
          {/* 0 or blank is the default, and it means never — deleting downloads
              on a timer is not something to opt people into silently. */}
          days (0 = keep)
        </label>
      </div>

      <input
        value={note}
        onChange={e => setNote(e.target.value)}
        placeholder="Note (optional) — why you grabbed this"
        className="w-full rounded bg-[var(--color-bg)] border border-white/10 px-2 py-1 text-sm"
      />

      {add.isError && (
        <div className="text-sm text-red-400">
          {(add.error as Error).message}
        </div>
      )}
      {errors.length > 0 && (
        <ul className="text-xs text-red-400 space-y-0.5">
          {errors.map((e, i) => (
            <li key={i}>
              <span className="font-mono">{e.input}</span> — {e.error}
            </li>
          ))}
        </ul>
      )}

      <div className="flex gap-2">
        <button
          onClick={() => add.mutate()}
          disabled={add.isPending || nothingToAdd}
          className="px-3 py-1.5 rounded bg-[var(--color-primary)] text-black text-sm font-medium disabled:opacity-40"
        >
          {add.isPending ? 'Adding…' : 'Add'}
        </button>
        <button
          onClick={onClose}
          className="px-3 py-1.5 rounded border border-white/10 text-sm"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
