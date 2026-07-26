// Pure helpers for the Food view, extracted so they can be unit-tested in the
// node environment (no jsdom).

export type MediaKind = 'image' | 'video';

/** Classify an upload by its MIME type (falling back to 'image'). */
export function mediaKind(mime: string): MediaKind {
  return mime.startsWith('video/') ? 'video' : 'image';
}

/** Render a 1-5 rating as filled/empty stars, or '' when unrated. */
export function ratingStars(rating: number | null | undefined): string {
  if (rating == null || rating < 1) return '';
  const n = Math.max(1, Math.min(5, Math.round(rating)));
  return '★'.repeat(n) + '☆'.repeat(5 - n);
}

/** An OpenStreetMap link for a lat/lng pair, or null if either is missing. */
export function mapLink(
  lat: number | null | undefined,
  lng: number | null | undefined
): string | null {
  if (lat == null || lng == null) return null;
  return `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lng}#map=17/${lat}/${lng}`;
}

/** A short title for a food entry/card, falling back through the fields. */
export function foodTitle(entry: {
  dish?: string | null;
  place?: string | null;
  notes?: string | null;
  rawContent?: string | null;
}): string {
  const raw =
    entry.dish || entry.place || entry.notes || entry.rawContent || '';
  const trimmed = raw.trim();
  if (!trimmed) return 'Food';
  const firstLine = trimmed.split('\n')[0];
  return firstLine.length > 80 ? firstLine.slice(0, 79) + '…' : firstLine;
}

/** Parse a JSON-array tag string (a food/recipe `tags` column) into a list. */
export function parseTags(tags: string | null | undefined): string[] {
  if (!tags) return [];
  try {
    const parsed = JSON.parse(tags);
    return Array.isArray(parsed)
      ? parsed.filter(t => typeof t === 'string')
      : [];
  } catch {
    return [];
  }
}
