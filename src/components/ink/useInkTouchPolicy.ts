import { useEffect, type RefObject } from 'react';

/** How a drawing surface shares touches with the page around it.
 *
 * The two are not a preference. They follow from whether there is anything
 * underneath to scroll, and getting them the wrong way round breaks the
 * surface completely: a Paper page that let a finger scroll would go nowhere,
 * and a newspaper column that did not would be unreadable.
 *
 *  - `exclusive` — the surface *is* the screen (the Paper editor, and the Study
 *    desk's embedded page). Nothing scrolls, so no touch on it may reach the
 *    browser as a gesture, and a finger is free to mean something else: a swipe
 *    flips the page, a two-finger tap toggles the eraser.
 *  - `scroll` — the surface is drawn over a scrolling column (the newspaper
 *    reader). A finger belongs to the browser in every tool, so it keeps native
 *    momentum scrolling and pinch-zoom, and the app claims no gestures at all.
 */
export type TouchPolicy = 'exclusive' | 'scroll';

// Safari alone reports what made a touch, and it is the only browser an Apple
// Pencil reaches us through; elsewhere the field is simply absent.
type StylusTouch = Touch & { touchType?: 'direct' | 'stylus' };

/** Things a touch can land on that are being *pressed*, not drawn over. */
const CONTROL = 'button, a, input, select, textarea, label, [role="button"]';

/** Did this touch begin on a control?
 *
 * A touch event's target is where the touch *started*, which is exactly the
 * question. It matters because cancelling a `touchmove` also cancels the click:
 * the Touch Events spec says a user agent that has had `touchstart` or
 * `touchmove` prevented must not dispatch the compatibility mouse events, and
 * WebKit obeys. A Pencil tap always wobbles a pixel or two, so it always
 * produces a `touchmove` — which is how guarding the Paper editor's whole stage
 * (the floating tool panel is inside it) left the pen unable to press
 * pen/highlighter/eraser at all, while the newspaper, whose guard is the page
 * box and whose panel floats outside it, was unaffected.
 */
const onControl = (target: EventTarget | null): boolean =>
  target instanceof Element && target.closest(CONTROL) !== null;

export const touchActionFor = (policy: TouchPolicy): string =>
  policy === 'exclusive' ? 'none' : 'pan-y pinch-zoom';

/**
 * Hold the browser off the touch stream so the Pencil writes instead.
 *
 * **Both policies need a native listener, and `exclusive` needing one is not
 * obvious.** It declares `touch-action: none`, which says exactly what it
 * wants — but WebKit ignores `touch-action` on an `<svg>`, and the ink layer
 * *is* an `<svg>`. So on iPadOS the declaration did nothing, the Pencil's
 * touches were WebKit's to pan with, and once that pan started the pen pointer
 * was *cancelled* mid-stroke: drawing on a Paper page (and so on the Study
 * desk's page) did not work at all, while the newspaper — which has had this
 * listener from the start — did. With a mouse there is no touch stream to
 * claim, which is why a desktop never saw it. The listener now rides on the
 * surface's own HTML wrapper, where `touch-action` is also honoured, so the
 * declaration and the listener say the same thing twice rather than once.
 *
 * What the two policies cancel differs, and follows from the same question as
 * the policy itself:
 *
 *  - `exclusive` — *every* touch, unconditionally. Nothing underneath scrolls
 *    or zooms, so there is no gesture to preserve; this is `touch-action: none`
 *    expressed the one way WebKit acts on. Pointer events are untouched, so the
 *    surface's own finger gestures still resolve.
 *  - `scroll` — an all-stylus touch while a marking tool is active, and *any*
 *    touch while a stroke is in flight, which is what stops a resting palm
 *    dragging the page out from under the nib. A mixed finger-and-stylus set is
 *    left alone — only two contacts can pinch-zoom, and that has to keep
 *    working. Read mode blocks nothing, so the Pencil scrolls there like a
 *    finger.
 *
 * It has to be a native non-passive listener under either: React attaches
 * `touchmove` passively, so an `onTouchMove` prop cannot `preventDefault` at
 * all. And `preventDefault` on `pointerdown` does not stop a WebKit pan —
 * cancelling the touch stream is the only thing that does.
 *
 * `exclusive` additionally refuses Safari's `gesturestart`/`gesturechange`. A
 * pinch is the one thing that can still claim the pen mid-stroke — the palm and
 * the nib are two contacts, and that is a pinch as far as WebKit is concerned —
 * and nothing under a surface that *is* the screen should be zooming anyway.
 * They are Safari-only and simply never fire elsewhere.
 *
 * **Which element is guarded matters as much as what it cancels.** A palm rests
 * where it likes, not only on the ink: Paper centres an A4 sheet with grey
 * margins either side, and a hand writing near an edge puts its palm on the
 * margin, outside the ink layer entirely. A guard mounted on the ink alone
 * never sees that touch, WebKit pairs it with the nib, and the pen pointer is
 * cancelled — a dot, and nothing more. So a surface with room around it passes
 * the whole stage as `guardRef`, exactly as a scrolling surface passes its page
 * box.
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
  /** The element to guard, which should be everything a palm can reach while
   * the pen is down — not merely the ink. For a scrolling surface it is the
   * page box, which also stays mounted when the ink layer does not. */
  guardRef: RefObject<Element | null>;
  /** Read at event time — true while a stroke is being drawn. */
  drawingRef: RefObject<boolean>;
}): void {
  useEffect(() => {
    const element = guardRef.current;
    if (!element) return;
    // Typed as a bare Event because the guard element is only known to be an
    // Element (an <svg> on one surface, a <div> on the other), and TypeScript's
    // touch event map is declared on HTMLElement.
    const onTouchMove = (event: Event) => {
      // Pressing a control is never the browser about to pan with the page, and
      // swallowing it costs the control its click. A stroke in flight outranks
      // that: nothing may take the pen away mid-stroke, a palm that happens to
      // land on the panel included.
      if (!drawingRef.current && onControl(event.target)) return;
      if (policy === 'exclusive') {
        event.preventDefault();
        return;
      }
      const touches = Array.from(
        (event as TouchEvent).touches
      ) as StylusTouch[];
      const stylus =
        touches.length > 0 && touches.every(t => t.touchType === 'stylus');
      if (drawingRef.current || (marking && stylus)) event.preventDefault();
    };
    element.addEventListener('touchmove', onTouchMove, { passive: false });
    if (policy !== 'exclusive') {
      return () => element.removeEventListener('touchmove', onTouchMove);
    }
    // Safari-only, and the last way a second contact can take the pen away.
    const onGesture = (event: Event) => event.preventDefault();
    element.addEventListener('gesturestart', onGesture, { passive: false });
    element.addEventListener('gesturechange', onGesture, { passive: false });
    return () => {
      element.removeEventListener('touchmove', onTouchMove);
      element.removeEventListener('gesturestart', onGesture);
      element.removeEventListener('gesturechange', onGesture);
    };
  }, [policy, marking, guardRef, drawingRef]);
}
