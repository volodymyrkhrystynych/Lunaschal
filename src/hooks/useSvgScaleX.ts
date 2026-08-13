import { useEffect, useState, type RefObject } from 'react';

/**
 * How many CSS pixels one viewBox x-unit currently covers.
 *
 * Both Lifestyle charts draw into a fixed 320-unit viewBox with
 * `preserveAspectRatio="none"` and a locked pixel height, which is what lets
 * them reflow to any width without a layout pass. The cost is that the two
 * axes scale independently: x stretches with the container while y stays at
 * 1.0, so a `<circle r>` — one radius, applied to both — paints as an ellipse.
 * On the desktop's full-width momentum card that is a ~3.5x stretch, a 9px dot
 * rendered 31px wide.
 *
 * The fix is to divide the marker's x-radius by this number rather than to
 * drop `preserveAspectRatio="none"`: the fixed viewBox is also what makes each
 * chart's `toViewX` pointer math correct, and changing the mapping would
 * silently select the wrong point on hover.
 *
 * Returns 1 until the element has been measured (first paint, and jsdom, where
 * every box is zero-sized), which is exactly the old behaviour — so a chart
 * renders correctly-at-320 rather than not at all while it waits.
 */
export function useSvgScaleX(
  ref: RefObject<SVGSVGElement | null>,
  viewWidth: number
): number {
  const [scaleX, setScaleX] = useState(1);

  // Deliberately no dependency array. Both charts mount their <svg> only once
  // the query resolves, so the ref is null on the first render — an effect
  // pinned to [] would measure nothing and never look again, leaving every
  // marker stuck at the 320px geometry. Re-measuring per render is a single
  // getBoundingClientRect, and the state guard below stops the loop.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const measure = () => {
      const width = el.getBoundingClientRect().width;
      // A detached or not-yet-laid-out element measures 0; dividing by the
      // scale that implies would put the marker's radius at infinity.
      const next = width > 0 ? width / viewWidth : 1;
      setScaleX(prev => (Math.abs(prev - next) < 0.001 ? prev : next));
    };

    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  });

  return scaleX;
}
