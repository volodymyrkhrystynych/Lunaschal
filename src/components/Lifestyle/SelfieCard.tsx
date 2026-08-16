import { useLayoutEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/hooks/api';
import { addDays, todayISO } from '@/lib/lifestyle';
import { CARD } from './card';

/** Days of history in the thumbnail strip — enough that a gap is obvious. */
const STRIP_DAYS = 14;

/** How many times a broken image load retries (with backoff) before giving up
 * and showing a static "failed" glyph instead. A transient network blip —
 * nothing to do with the file itself, which is still fine on disk — otherwise
 * leaves a thumbnail permanently broken until the whole card remounts. */
const MAX_IMAGE_RETRIES = 3;

/** An `<img>` that retries itself on a load error instead of sitting broken.
 * `key={attempt}` forces React to mount a fresh `<img>`, and the cache-busting
 * query param on retries guarantees a real network request rather than
 * whatever the browser cached for the failed attempt. */
function RetryingImage({
  src,
  alt,
  className,
}: {
  src: string;
  alt: string;
  className: string;
}) {
  const [attempt, setAttempt] = useState(0);

  if (attempt > MAX_IMAGE_RETRIES) {
    return (
      <div
        className={`${className} flex items-center justify-center text-[var(--color-text-muted)]`}
        title="Failed to load"
      >
        ⚠
      </div>
    );
  }

  return (
    <img
      key={attempt}
      src={attempt === 0 ? src : `${src}?retry=${attempt}`}
      alt={alt}
      className={className}
      onError={() => {
        const delay = 300 * 2 ** attempt;
        setTimeout(() => setAttempt(a => a + 1), delay);
      }}
    />
  );
}

/**
 * The daily selfie: capture, plus a strip of recent days so a missed one is
 * visible at a glance.
 *
 * **Capture always hands off to the device camera app** (`capture="user"` on a
 * file input). An in-page `getUserMedia` preview used to sit behind "Take
 * selfie"; on an iPad that is the wrong affordance — the OS camera is better at
 * being a camera than a `<video>` in a card is — so both buttons now take the
 * same native path and the live-preview machinery is gone.
 *
 * **Nothing in the strip deletes.** A thumbnail used to be a delete button, and
 * a stray tap cost real photos. The only in-app way to replace a day is
 * retaking it that same day (the upload route overwrites per date); removing a
 * selfie outright is a deliberate database operation. Tapping a thumbnail now
 * just enlarges it.
 */
export function SelfieCard() {
  const queryClient = useQueryClient();
  const cameraRef = useRef<HTMLInputElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const stripRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<string | null>(null);

  const { data: selfies = [] } = useQuery({
    queryKey: ['lifestyle', 'selfies'],
    queryFn: () => api.lifestyle.selfies.list(120),
  });

  // The strip is oldest-to-newest, so the fixed 14-day layout is scrolled to
  // its right edge on mount — today's slot (filled or still a dashed gap) is
  // what answers "did I log one today", not day 14 back.
  useLayoutEffect(() => {
    const el = stripRef.current;
    if (el) el.scrollLeft = el.scrollWidth;
  }, []);

  const upload = useMutation({
    mutationFn: (image: Blob) => api.lifestyle.selfies.upload(image),
    onSuccess: () => {
      setError(null);
      queryClient.invalidateQueries({ queryKey: ['lifestyle', 'selfies'] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const pick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) upload.mutate(file);
    e.target.value = '';
  };

  // A fixed run of recent days, so a day with no selfie shows as an empty slot
  // rather than silently collapsing out of the strip.
  const today = todayISO();
  const byDate = new Map(selfies.map(s => [s.date, s]));
  const strip = Array.from({ length: STRIP_DAYS }, (_, i) => {
    const date = addDays(today, -(STRIP_DAYS - 1 - i));
    return { date, selfie: byDate.get(date) ?? null };
  });
  const todaySelfie = byDate.get(today) ?? null;
  const previewSelfie = preview ? (byDate.get(preview) ?? null) : null;

  return (
    <section className={CARD}>
      <div className="flex items-baseline justify-between gap-2 mb-3">
        <h2 className="text-lg font-semibold text-[var(--color-text)]">
          Daily selfie
        </h2>
        {todaySelfie && (
          <span className="text-xs text-[var(--color-text-muted)]">
            Logged today
          </span>
        )}
      </div>

      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => cameraRef.current?.click()}
          disabled={upload.isPending}
          className="flex-1 px-3 py-2 rounded bg-[var(--color-primary)] text-white text-sm disabled:opacity-40 min-h-[44px]"
        >
          {upload.isPending
            ? 'Saving…'
            : todaySelfie
              ? 'Retake'
              : 'Take selfie'}
        </button>
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          disabled={upload.isPending}
          className="px-3 py-2 rounded border border-white/10 text-[var(--color-text)] text-sm disabled:opacity-40 min-h-[44px]"
        >
          Choose photo
        </button>
        {/* Same control twice, differing only in `capture`: "Take selfie"
            asks the OS for the front camera, "Choose photo" for the library. */}
        <input
          ref={cameraRef}
          data-testid="selfie-camera-input"
          type="file"
          accept="image/*"
          capture="user"
          className="hidden"
          onChange={pick}
        />
        <input
          ref={fileRef}
          data-testid="selfie-file-input"
          type="file"
          accept="image/*"
          className="hidden"
          onChange={pick}
        />
      </div>

      {error && (
        <div className="mt-2 text-xs text-[var(--color-accent)]">{error}</div>
      )}

      {previewSelfie && (
        <div className="mt-3">
          <RetryingImage
            src={previewSelfie.url}
            alt={`Selfie from ${previewSelfie.date}`}
            className="w-full max-h-64 rounded object-contain bg-black/20"
          />
          <div className="mt-1 flex items-center justify-between gap-2 text-xs text-[var(--color-text-muted)]">
            <span>{previewSelfie.date}</span>
            <button
              type="button"
              onClick={() => setPreview(null)}
              className="hover:text-[var(--color-text)]"
            >
              Close
            </button>
          </div>
        </div>
      )}

      <div ref={stripRef} className="mt-3 flex gap-1 overflow-x-auto pb-1">
        {strip.map(({ date, selfie }) => (
          <div key={date} className="shrink-0 text-center">
            {selfie ? (
              <button
                type="button"
                onClick={() => setPreview(p => (p === date ? null : date))}
                aria-pressed={preview === date}
                title={`${date} — show larger`}
                className={`block w-12 h-12 rounded overflow-hidden border hover:border-[var(--color-primary)] ${
                  preview === date
                    ? 'border-[var(--color-primary)]'
                    : 'border-white/10'
                }`}
              >
                <RetryingImage
                  src={selfie.url}
                  alt={`Selfie from ${date}`}
                  className="w-full h-full object-cover"
                />
              </button>
            ) : (
              <div
                title={`${date} — no selfie`}
                className="w-12 h-12 rounded border border-dashed border-white/10 bg-[var(--color-bg)]"
              />
            )}
            <div className="mt-0.5 text-[10px] text-[var(--color-text-muted)]">
              {date.slice(8)}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
