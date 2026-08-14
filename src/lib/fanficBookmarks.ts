// Pure helpers for chapter bookmarks (favorite / continue) — unit-tested in
// node, no DOM.

import type { FicChapterSummary } from '@/hooks/api';

/** Fraction (0–1) of the way down a scrollable element, clamped. A container
 * shorter than its viewport (nothing to scroll) reads as 0. */
export function scrollFraction(
  scrollTop: number,
  scrollHeight: number,
  clientHeight: number
): number {
  const range = scrollHeight - clientHeight;
  if (range <= 0) return 0;
  return Math.max(0, Math.min(1, scrollTop / range));
}

/** Inverse of scrollFraction — the scrollTop to pass to `scrollTo`. */
export function scrollTopForFraction(
  fraction: number,
  scrollHeight: number,
  clientHeight: number
): number {
  const range = scrollHeight - clientHeight;
  if (range <= 0) return 0;
  return Math.max(0, Math.min(1, fraction)) * range;
}

/**
 * Which chapter the reader should open on: an explicit deep-link target beats
 * the fic's one continue bookmark, which beats the auto-tracked last-read
 * chapter, which beats just opening the first chapter. Any id pointing at a
 * chapter that no longer exists (deleted/re-imported) is skipped rather than
 * erroring. `chapters` must already be in reading order (see
 * `orderChapters` in `fanfic.ts`) since the "first chapter" fallback is
 * simply `chapters[0]`.
 */
export function resolveInitialChapter(
  chapters: FicChapterSummary[],
  opts: {
    targetChapterId?: string | null;
    continueChapterId?: string | null;
    lastReadChapterId?: string | null;
  }
): FicChapterSummary | null {
  if (chapters.length === 0) return null;
  const byId = (id: string | null | undefined) =>
    id ? (chapters.find(c => c.id === id) ?? null) : null;
  return (
    byId(opts.targetChapterId) ??
    byId(opts.continueChapterId) ??
    byId(opts.lastReadChapterId) ??
    chapters[0]
  );
}
