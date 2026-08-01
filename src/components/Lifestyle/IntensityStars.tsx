import {
  INTENSITY_MAX,
  intensityLabel,
  intensityStars,
  intensityText,
} from '@/lib/lifestyle';

/**
 * Workout intensity as five stars.
 *
 * It replaced a 1–10 RPE field, which was too subjective to rate the same way
 * twice. The written meanings are the whole point of the change, so they are
 * never left implicit: the picker prints the selected one, every star carries
 * it as its title/aria-label, and the readout below spells the rating out for
 * screen readers rather than leaving them a run of ★ glyphs.
 */

const STARS = Array.from({ length: INTENSITY_MAX }, (_, i) => i + 1);

interface IntensityPickerProps {
  /** Empty string when nothing is picked — the draft stores form fields as text. */
  value: string;
  onChange: (value: string) => void;
}

export function IntensityPicker({ value, onChange }: IntensityPickerProps) {
  const current = Number(value) || 0;
  return (
    <div>
      <span
        id="intensity-label"
        className="text-xs text-[var(--color-text-muted)]"
      >
        Intensity
      </span>
      <div
        role="radiogroup"
        aria-labelledby="intensity-label"
        className="mt-1 flex items-center gap-1"
      >
        {STARS.map(star => {
          const label = intensityLabel(star) as string;
          const selected = current === star;
          return (
            <button
              key={star}
              type="button"
              role="radio"
              aria-checked={selected}
              // Both the number and its meaning, so the control is usable
              // without ever seeing the glyphs.
              aria-label={`${star} of ${INTENSITY_MAX} — ${label}`}
              title={`${star} — ${label}`}
              // Tapping the current rating clears it: intensity is optional, and
              // a phone has no other way to take a mis-tap back.
              onClick={() => onChange(selected ? '' : String(star))}
              className={`w-9 h-9 rounded text-lg leading-none transition-colors ${
                star <= current
                  ? 'text-[var(--color-primary)]'
                  : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
              }`}
            >
              {star <= current ? '★' : '☆'}
            </button>
          );
        })}
      </div>
      <div className="mt-0.5 text-xs text-[var(--color-text-muted)] min-h-[1rem]">
        {intensityLabel(current) ?? 'Not rated'}
      </div>
    </div>
  );
}

/** Read-only intensity: stars for the eye, words for everything else. */
export function IntensityStars({
  value,
  className = 'text-xs text-[var(--color-text-muted)]',
}: {
  value: number;
  className?: string;
}) {
  const text = intensityText(value) as string;
  return (
    <span className={className} title={`Intensity ${text}`}>
      <span aria-hidden="true">{intensityStars(value)}</span>
      <span className="sr-only">{`Intensity ${text}`}</span>
    </span>
  );
}
