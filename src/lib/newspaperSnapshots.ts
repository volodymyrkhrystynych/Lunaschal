// Which pages of an issue need a fresh picture rendering, and how big it is.
//
// The pictures the Journal feed shows are made by the reader itself: there is
// no PDF rasterizer on the server (backend/newspapers/issues.py says why), so
// the only machine that can draw a page is the one already drawing it. This
// module is the half of that worker which needs no canvas — kept here so it
// can be tested in the node environment, where jsdom's missing 2D context and
// Path2D would otherwise make the rule untestable.

import { strokesOn, type IssueMarkup } from '@/lib/newspaperMarkup';

/** Width of a rendered page picture, in pixels.
 *
 * Fixed, and deliberately not the width the page happens to be on screen: the
 * same page must not produce a different thumbnail on a phone and on a laptop
 * — the lesson already recorded at src/components/Paper/PaperSurface.tsx's
 * SNAPSHOT_WIDTH. Smaller than Paper's 1240 because a broadsheet is shown 128
 * px tall in the feed's strip and full-screen in the lightbox, and nothing in
 * between.
 */
export const SNAPSHOT_WIDTH = 1000;

/** JPEG, not Paper's PNG: a rasterized newspaper page is a photograph of type,
 * and PNG of one is several times the bytes for nothing visible. */
export const SNAPSHOT_MIME = 'image/jpeg';
export const SNAPSHOT_QUALITY = 0.75;

/** What the reader has already put on the server for one page. */
export interface RenderedPage {
  /** Strokes the page had when that picture was made. A page whose ink has
   * changed since needs a new one; `null` means the picture came from the
   * server and this session has no idea what was on it. */
  strokes: number | null;
}

/**
 * Pages whose picture is missing or out of date, in ascending page order.
 *
 * Page 1 is always wanted — it is the issue's cover, and the cover is what
 * gives a paper that was read and never written on a picture in the feed.
 * Beyond that: every page carrying ink whose stroke count differs from the
 * picture we last uploaded for it.
 *
 * Derived from the markup each time it is asked rather than accumulated into a
 * queue, which is what makes erasing safe: a page rubbed clean simply stops
 * being in the answer, so a picture of ink that no longer exists is never
 * uploaded — and the server's prune has already removed the file.
 */
export function pagesNeedingSnapshot(
  markup: IssueMarkup,
  rendered: Map<number, RenderedPage>,
  pageCount: number
): number[] {
  const wanted = new Set<number>();
  if (pageCount >= 1) wanted.add(1);
  for (const page of markup.pages.keys()) {
    if (page >= 1 && page <= pageCount && strokesOn(markup, page).length > 0) {
      wanted.add(page);
    }
  }
  const out: number[] = [];
  for (const page of [...wanted].sort((a, b) => a - b)) {
    const have = rendered.get(page);
    if (!have) {
      out.push(page);
      continue;
    }
    // A picture from the server is trusted until this session changes the
    // page: we cannot know what was on it, and re-rendering every marked page
    // on every open would re-upload an entire issue for nothing.
    if (
      have.strokes !== null &&
      have.strokes !== strokesOn(markup, page).length
    ) {
      out.push(page);
    }
  }
  return out;
}
