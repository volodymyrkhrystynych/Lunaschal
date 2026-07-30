import { useEffect, useState } from 'react';
import { Library } from './Library';
import { Reader } from './Reader';
import {
  getStoredFicTarget,
  setStoredFicTarget,
} from '../../lib/fanficPersistence';

export interface FicTarget {
  ficId: string;
  chapterId?: string;
}

interface FanficProps {
  /** Cross-view jump target (e.g. a journal chip was clicked). */
  target?: FicTarget | null;
  onTargetConsumed?: () => void;
}

export function Fanfic({ target, onTargetConsumed }: FanficProps) {
  const [openFic, setOpenFic] = useState<FicTarget | null>(() =>
    getStoredFicTarget()
  );

  useEffect(() => {
    if (target) {
      setOpenFic(target);
      onTargetConsumed?.();
    }
  }, [target, onTargetConsumed]);

  useEffect(() => {
    setStoredFicTarget(openFic ? { ficId: openFic.ficId } : null);
  }, [openFic]);

  if (openFic) {
    return (
      <Reader
        ficId={openFic.ficId}
        initialChapterId={openFic.chapterId}
        onBack={() => setOpenFic(null)}
      />
    );
  }
  return <Library onOpen={ficId => setOpenFic({ ficId })} />;
}
