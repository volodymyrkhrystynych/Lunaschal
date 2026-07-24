import { useState } from 'react';
import { useIsMobile } from './useMediaQuery';

/**
 * Two-panel (list + detail) views show both panes side by side on desktop, but
 * on a phone there's only room for one at a time. This hook decides which pane
 * is visible: on desktop both are always shown (unchanged behavior); on mobile
 * exactly one, and callers flip between them via openDetail / openList.
 */
export function useMasterDetail() {
  const isMobile = useIsMobile();
  const [pane, setPane] = useState<'list' | 'detail'>('list');

  return {
    isMobile,
    showList: !isMobile || pane === 'list',
    showDetail: !isMobile || pane === 'detail',
    openDetail: () => setPane('detail'),
    openList: () => setPane('list'),
  };
}
