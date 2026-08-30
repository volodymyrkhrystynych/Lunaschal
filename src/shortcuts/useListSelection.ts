import { useEffect, useRef, useState } from 'react';
import { useShortcuts } from './ShortcutProvider';

/**
 * Shared state for the single-selection, arrow-key-navigable lists used
 * throughout the app (Journal, Meetings, Learning queue/browse, Food log/
 * recipes, Fanfic library). Owns the selected index, clamps it when the list
 * shrinks, and exposes `next`/`prev` to wire into a `useShortcutScope` call
 * alongside a view's other scope handlers.
 *
 * `scopeDepth` is the shortcut scope depth (from `keymap`/`ShortcutProvider`)
 * at which this list is the active navigation target — `isSelected` only
 * reports true once the shared `level` has reached at least that depth, so a
 * row doesn't render as selected while a shallower scope (e.g. the sidebar)
 * has focus.
 */
export function useListSelection(
  length: number | undefined,
  scopeDepth: number
) {
  const [selIndex, setSelIndex] = useState(0);
  const { level } = useShortcuts();

  useEffect(() => {
    setSelIndex(i => Math.min(i, Math.max((length ?? 1) - 1, 0)));
  }, [length]);

  const next = () =>
    setSelIndex(i => Math.min(i + 1, Math.max((length ?? 1) - 1, 0)));
  const prev = () => setSelIndex(i => Math.max(i - 1, 0));

  const isSelected = (idx: number) => level >= scopeDepth && idx === selIndex;

  /**
   * A callback ref that scrolls the row into view when it is the selected one.
   *
   * The returned closures are cached per index. React compares a callback ref by
   * identity and, when it changes, calls the old one with `null` and the new one
   * with the node — so a fresh closure per row per render meant every row in the
   * list detached and re-attached its ref on every render, and the selected row
   * re-ran `scrollIntoView` (a forced synchronous layout) each time. In the
   * Journal that happened on every keystroke in the compose box.
   *
   * The cache is cleared whenever the selection or the scope depth changes,
   * which is exactly when the behaviour of these closures differs — so a stale
   * closure can never be handed out.
   */
  const refCache = useRef(new Map<number, (el: HTMLElement | null) => void>());
  const cacheKey = `${level}:${selIndex}`;
  const lastKey = useRef(cacheKey);
  if (lastKey.current !== cacheKey) {
    lastKey.current = cacheKey;
    refCache.current = new Map();
  }

  const scrollSelectedIntoView = (idx: number) => {
    const cached = refCache.current.get(idx);
    if (cached) return cached;
    const fn = (el: HTMLElement | null) => {
      if (el && isSelected(idx)) el.scrollIntoView({ block: 'nearest' });
    };
    refCache.current.set(idx, fn);
    return fn;
  };

  return {
    selIndex,
    setSelIndex,
    next,
    prev,
    isSelected,
    scrollSelectedIntoView,
  };
}
