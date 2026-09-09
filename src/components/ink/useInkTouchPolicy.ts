import { useEffect, type RefObject } from 'react';

/** How a drawing surface shares touches with the page around it.
 *
 * The two are not a preference. They follow from whether there is anything
 * underneath to scroll, and getting them the wrong way round breaks the
 * surface completely: a Paper page that let a finger scroll would go nowhere,
 * and a newspaper column that did not would be unreadable.
 *
 *  - `exclusive` — the surface *is* the screen (the Paper editor, and the Study
 *    desk's embedded page). Nothing scrolls, so `touch-action: none` and a
 *    finger is free to mean something else: a swipe flips the page, a
 *    two-finger tap toggles the eraser.
 *  - `scroll` — the surface is drawn over a scrolling column (the newspaper
 *    reader). A finger belongs to the browser in every tool, so it keeps native
 *    momentum scrolling and pinch-zoom, and the app claims no gestures at all.
 */
export type TouchPolicy = 'exclusive' | 'scroll';

// Safari alone reports what made a touch, and it is the only browser an Apple
// Pencil reaches us through; elsewhere the field is simply absent.
type StylusTouch = Touch & { touchType?: 'direct' | 'stylus' };

export const touchActionFor = (policy: TouchPolicy): string =>
  policy === 'exclusive' ? 'none' : 'pan-y pinch-zoom';

/**
 * Under `scroll`, hold the Pencil off the scroller so it writes instead.
 *
 * iPadOS gives exactly one way to do that: cancel the touch stream itself.
 * `touch-action` is not enough — WebKit ignores it on an `<svg>`, and even
 * where it is honoured it cannot tell a pen from a finger — and
 * `preventDefault` on `pointerdown` does not stop a WebKit scroll either. Once
 * that scroll starts the pen pointer is *cancelled* mid-stroke, which is
 * exactly what "the Pencil scrolls instead of writing" was.
 *
 * So: an all-stylus touch is cancelled while a marking tool is active, and
 * *any* touch is cancelled while a stroke is in flight, which is what stops a
 * resting palm dragging the page out from under the nib. A mixed
 * finger-and-stylus set is left alone — only two contacts can pinch-zoom, and
 * that has to keep working. Read mode blocks nothing, so the Pencil scrolls
 * there like a finger.
 *
 * It has to be a native non-passive listener: React attaches `touchmove`
 * passively, so an `onTouchMove` prop cannot `preventDefault` at all.
 *
 * `exclusive` needs none of this — `touch-action: none` already means no touch
 * on the surface scrolls anything.
 */
export function useInkTouchPolicy({
  policy,
  marking,
  guardRef,
  drawingRef,
}: {
  policy: TouchPolicy;
  /** Whether a tool that lays down ink is selected. */
  marking: boolean;
  /** The element to guard. For a scrolling surface this is the page box, not
   * the canvas: it stays mounted when the canvas does not. */
  guardRef: RefObject<HTMLElement | null>;
  /** Read at event time — true while a stroke is being drawn. */
  drawingRef: RefObject<boolean>;
}): void {
  useEffect(() => {
    if (policy !== 'scroll') return;
    const element = guardRef.current;
    if (!element) return;
    const onTouchMove = (event: TouchEvent) => {
      const touches = Array.from(event.touches) as StylusTouch[];
      const stylus =
        touches.length > 0 && touches.every(t => t.touchType === 'stylus');
      if (drawingRef.current || (marking && stylus)) event.preventDefault();
    };
    element.addEventListener('touchmove', onTouchMove, { passive: false });
    return () => element.removeEventListener('touchmove', onTouchMove);
  }, [policy, marking, guardRef, drawingRef]);
}
