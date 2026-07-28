import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import type { FoodEntry } from '../../hooks/api';
import { useShortcutScope } from '../../shortcuts/ShortcutProvider';
import { useListSelection } from '../../shortcuts/useListSelection';
import { ratingStars, foodTitle, mapLink } from '../../lib/food';
import { FoodCapture } from './FoodCapture';

const splitTagInput = (input: string): string[] =>
  input
    .split(',')
    .map(t => t.trim().toLowerCase())
    .filter(Boolean);

const formatDate = (date: string) =>
  new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(new Date(date));

function StarPicker({
  value,
  onChange,
}: {
  value: number | null;
  onChange: (v: number | null) => void;
}) {
  return (
    <div className="flex items-center gap-1">
      {[1, 2, 3, 4, 5].map(n => (
        <button
          key={n}
          type="button"
          onClick={() => onChange(value === n ? null : n)}
          className={`text-lg leading-none ${
            value && n <= value
              ? 'text-[var(--color-primary)]'
              : 'text-[var(--color-text-muted)]'
          }`}
          title={`${n} star${n > 1 ? 's' : ''}`}
        >
          {value && n <= value ? '★' : '☆'}
        </button>
      ))}
    </div>
  );
}

function FoodEntryCard({
  entry,
  selected,
  onZoom,
}: {
  entry: FoodEntry;
  selected: boolean;
  onZoom: (url: string) => void;
}) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [showRaw, setShowRaw] = useState(false);
  const [dish, setDish] = useState(entry.dish ?? '');
  const [place, setPlace] = useState(entry.place ?? '');
  const [notes, setNotes] = useState(entry.notes ?? '');
  const [rating, setRating] = useState<number | null>(entry.rating);
  const [tags, setTags] = useState('');

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ['food'] });

  const update = useMutation({
    mutationFn: () =>
      api.food.update(entry.id, {
        dish: dish.trim() || null,
        place: place.trim() || null,
        notes: notes.trim() || null,
        rating,
        tags: splitTagInput(tags),
      }),
    onSuccess: () => {
      invalidate();
      setEditing(false);
    },
  });

  const remove = useMutation({
    mutationFn: () => api.food.delete(entry.id),
    onSuccess: invalidate,
  });

  const startEdit = () => {
    setDish(entry.dish ?? '');
    setPlace(entry.place ?? '');
    setNotes(entry.notes ?? '');
    setRating(entry.rating);
    setTags(entryTags(entry).join(', '));
    setEditing(true);
  };

  const stars = ratingStars(entry.rating);
  const tagList = entryTags(entry);
  const geoLink = mapLink(entry.latitude, entry.longitude);
  // The original transcript is worth surfacing only when the polished notes
  // actually differ from what was said (AI ran and rewrote it).
  const hasOriginal = !!entry.rawContent && entry.rawContent !== entry.notes;

  return (
    <div
      className={`p-4 bg-[var(--color-surface)] rounded-lg border ${
        selected ? 'border-[var(--color-primary)]' : 'border-white/10'
      }`}
    >
      {entry.media.length > 0 && (
        <div className="flex gap-2 overflow-x-auto mb-3 pb-1">
          {entry.media.map(m =>
            m.kind === 'video' ? (
              <video
                key={m.id}
                src={m.url}
                controls
                className="h-32 rounded border border-white/10 shrink-0"
              />
            ) : (
              <img
                key={m.id}
                src={m.url}
                alt=""
                onClick={() => onZoom(m.url)}
                className="h-32 rounded border border-white/10 shrink-0 object-cover cursor-zoom-in"
              />
            )
          )}
        </div>
      )}

      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-base font-bold text-[var(--color-text)]">
            {foodTitle(entry)}
          </div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-[var(--color-text-muted)] mt-0.5">
            <span>{formatDate(entry.createdAt)}</span>
            {entry.place && <span>📍 {entry.place}</span>}
            {geoLink && (
              <a
                href={geoLink}
                target="_blank"
                rel="noreferrer"
                className="underline hover:text-[var(--color-text)]"
                title="Open location in a map"
              >
                🗺️ map
              </a>
            )}
            {stars && (
              <span className="text-[var(--color-primary)]">{stars}</span>
            )}
          </div>
        </div>
        <div className="flex gap-2 shrink-0">
          <button
            onClick={startEdit}
            className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
          >
            Edit
          </button>
          <button
            onClick={() => remove.mutate()}
            className="text-sm text-red-400 hover:text-red-300"
          >
            Delete
          </button>
        </div>
      </div>

      {editing ? (
        <div className="mt-3 space-y-2">
          <input
            value={dish}
            onChange={e => setDish(e.target.value)}
            placeholder="Dish"
            className="w-full bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none border border-white/10 rounded p-2"
          />
          <input
            value={place}
            onChange={e => setPlace(e.target.value)}
            placeholder="Where"
            className="w-full bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none border border-white/10 rounded p-2"
          />
          <textarea
            value={notes}
            onChange={e => setNotes(e.target.value)}
            placeholder="Notes"
            rows={3}
            className="w-full bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] resize-none focus:outline-none border border-white/10 rounded p-2"
          />
          <div className="flex items-center gap-3">
            <span className="text-sm text-[var(--color-text-muted)]">
              Rating
            </span>
            <StarPicker value={rating} onChange={setRating} />
          </div>
          <input
            value={tags}
            onChange={e => setTags(e.target.value)}
            placeholder="Tags, comma separated"
            className="w-full bg-transparent text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none border border-white/10 rounded p-2"
          />
          <div className="flex justify-end gap-2">
            <button
              onClick={() => setEditing(false)}
              className="px-3 py-1 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            >
              Cancel
            </button>
            <button
              onClick={() => update.mutate()}
              disabled={update.isPending}
              className="px-3 py-1 bg-[var(--color-primary)] text-white rounded hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
            >
              Save
            </button>
          </div>
        </div>
      ) : (
        <>
          {(entry.notes || entry.rawContent) && (
            <div className="content-text text-[var(--color-text)] whitespace-pre-wrap mt-2">
              {showRaw ? entry.rawContent : (entry.notes ?? entry.rawContent)}
            </div>
          )}
          {hasOriginal && (
            <button
              onClick={() => setShowRaw(s => !s)}
              className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] mt-1"
            >
              {showRaw ? 'Show cleaned-up' : 'Show original transcript'}
            </button>
          )}
          <div className="flex flex-wrap items-center gap-1.5 mt-2">
            {entry.recipe && (
              <span className="px-2 py-0.5 text-xs rounded border border-[var(--color-primary)]/40 text-[var(--color-primary)] bg-[var(--color-primary)]/10">
                🍳 {entry.recipe.title}
              </span>
            )}
            {tagList.map(tag => (
              <span
                key={tag}
                className="px-2 py-0.5 text-xs rounded border border-white/15 text-[var(--color-text-muted)]"
              >
                #{tag}
              </span>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function entryTags(entry: FoodEntry): string[] {
  if (!entry.tags) return [];
  try {
    const parsed = JSON.parse(entry.tags);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

/** The "Log" section of the Food tab: capture form + reverse-chronological feed. */
export function FoodLog() {
  const [selectedTag, setSelectedTag] = useState<string | null>(null);
  const [showCapture, setShowCapture] = useState(true);
  const [zoom, setZoom] = useState<string | null>(null);

  const { data: tags } = useQuery({
    queryKey: ['food', 'tags'],
    queryFn: api.food.tags,
  });

  const { data: entries, isLoading } = useQuery({
    queryKey: ['food', 'list', { tag: selectedTag }],
    queryFn: () => api.food.list({ tag: selectedTag ?? undefined }),
  });

  const { next, prev, isSelected, scrollSelectedIntoView } = useListSelection(
    entries?.length,
    1
  );

  useShortcutScope(1, {
    next,
    prev,
    create: () => setShowCapture(true),
  });

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
      <div className="flex items-center justify-end mb-3">
        <button
          onClick={() => setShowCapture(s => !s)}
          className="px-4 py-2 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 transition-colors"
        >
          {showCapture ? 'Hide' : '+ Log food'}
        </button>
      </div>

      {showCapture && <FoodCapture />}

      {(tags?.length ?? 0) > 0 && (
        <div className="tag-row flex flex-wrap gap-1.5 mb-3">
          {tags?.map(tag => (
            <button
              key={tag.name}
              onClick={() =>
                setSelectedTag(selectedTag === tag.name ? null : tag.name)
              }
              className={`px-3 py-1 text-xs rounded-full border transition-colors ${
                selectedTag === tag.name
                  ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/20 text-[var(--color-primary)]'
                  : 'border-white/20 text-[var(--color-text-muted)] hover:border-white/40 hover:text-[var(--color-text)]'
              }`}
            >
              #{tag.name}
              <span className="ml-1 opacity-60">({tag.count})</span>
            </button>
          ))}
        </div>
      )}

      <div className="flex-1 overflow-y-auto space-y-4">
        {isLoading && (
          <div className="text-[var(--color-text-muted)]">Loading...</div>
        )}
        {entries?.map((entry, idx) => (
          <div key={entry.id} ref={scrollSelectedIntoView(idx)}>
            <FoodEntryCard
              entry={entry}
              selected={isSelected(idx)}
              onZoom={setZoom}
            />
          </div>
        ))}
        {entries?.length === 0 && !isLoading && (
          <div className="text-center text-[var(--color-text-muted)] py-12">
            {selectedTag
              ? 'No entries with this tag'
              : 'No food logged yet. Snap a photo or say what you ate!'}
          </div>
        )}
      </div>

      {zoom && (
        <div
          className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-4"
          onClick={() => setZoom(null)}
        >
          <img
            src={zoom}
            alt=""
            className="max-w-full max-h-full rounded object-contain"
          />
        </div>
      )}
    </div>
  );
}
