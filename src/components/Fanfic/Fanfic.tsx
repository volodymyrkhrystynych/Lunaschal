import { useEffect, useState } from 'react';
import { Library } from './Library';
import { Reader } from './Reader';

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
  const [openFic, setOpenFic] = useState<FicTarget | null>(null);

  useEffect(() => {
    if (target) {
      setOpenFic(target);
      onTargetConsumed?.();
    }
  }, [target, onTargetConsumed]);

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
