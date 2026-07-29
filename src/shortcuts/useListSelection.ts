import { useEffect, useState } from 'react';
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

  const scrollSelectedIntoView = (idx: number) => (el: HTMLElement | null) => {
    if (el && isSelected(idx)) el.scrollIntoView({ block: 'nearest' });
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
