import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { useShortcutScope } from '../../shortcuts/ShortcutProvider';
import { useMasterDetail } from '@/hooks/useMasterDetail';
import { MasterDetailBack } from '@/components/MasterDetailBack';
import { IdeaList } from './IdeaList';
import { IdeaDetail } from './IdeaDetail';

interface IdeasProps {
  /** Open straight onto this idea — the Journal's link back into a dictated
   *  idea. Cleared through `onTargetConsumed` so it fires once, not on every
   *  return to the tab. */
  target?: { ideaId: string } | null;
  onTargetConsumed?: () => void;
  /** Switch to the Journal, at the entry a dictated idea's recording became. */
  onOpenEntry?: (entryId: string) => void;
}

export function Ideas({
  target,
  onTargetConsumed,
  onOpenEntry,
}: IdeasProps = {}) {
  const [selectedId, setSelectedId] = useState<string>('');
  const { isMobile, showList, showDetail, openDetail, openList } =
    useMasterDetail();

  useEffect(() => {
    if (!target) return;
    setSelectedId(target.ideaId);
    openDetail();
    onTargetConsumed?.();
    // openDetail/onTargetConsumed are re-created per render; the target is the
    // only thing that should re-run this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target]);

  const { data: ideas, isLoading } = useQuery({
    queryKey: ['ideas'],
    queryFn: api.ideas.list,
  });

  const step = (delta: number) => {
    const rows = ideas ?? [];
    if (rows.length === 0) return;
    const current = rows.findIndex(i => i.id === selectedId);
    const nextIndex =
      current === -1 ? 0 : (current + delta + rows.length) % rows.length;
    setSelectedId(rows[nextIndex]!.id);
  };

  // Scope 1 owns the list; IdeaDetail registers scope 2. Keeping the numbers
  // contiguous is what lets `nav.in` descend from here into the detail pane.
  // IdeaCapture also registers on scope 1 (create/record) — disjoint method
  // sets stack safely, the same arrangement Writing's nav uses.
  useShortcutScope(1, {
    next: () => step(1),
    prev: () => step(-1),
  });

  const handleSelect = (id: string) => {
    setSelectedId(id);
    openDetail();
  };

  const listShown = isMobile ? showList : true;
  const detailShown = isMobile ? showDetail : true;

  return (
    <div className="flex-1 flex overflow-hidden">
      {listShown && (
        <div
          className={`${isMobile ? 'w-full' : 'w-80 shrink-0'} border-r border-white/10 bg-[var(--color-surface)] flex flex-col overflow-hidden`}
        >
          <IdeaList
            ideas={ideas ?? []}
            isLoading={isLoading}
            selectedId={selectedId}
            onSelect={handleSelect}
          />
        </div>
      )}

      {detailShown && (
        <div className="flex-1 flex flex-col overflow-hidden">
          <MasterDetailBack onClick={openList} label="Ideas" />
          {selectedId ? (
            <IdeaDetail
              key={selectedId}
              ideaId={selectedId}
              onOpenEntry={onOpenEntry}
            />
          ) : (
            <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)]">
              Select an idea, or capture a new one
            </div>
          )}
        </div>
      )}
    </div>
  );
}
