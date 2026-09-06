import { useEffect, useState } from 'react';
import { useIsLargeScreen } from '../../hooks/useMediaQuery';
import type { StudySource } from '../../lib/study';
import { StudyDesk } from './StudyDesk';
import { StudyLibrary } from './StudyLibrary';

/**
 * The Study tab: a library of sources, and a two-pane desk for reading one
 * beside your notes.
 *
 * Library and desk swap for each other rather than sitting side by side — the
 * same shape Paper's explorer and editor use, and with a fixed 50/50 split
 * there is no room for a third column anyway.
 *
 * **The tab exists on every device; the desk does not.** Below 1024px this is
 * the library alone — somewhere to drop a YouTube link or archive an article
 * from the phone you found it on, ready to be read later on a screen that fits
 * two panes. Hiding the whole tab (which is what `largeOnly` used to do) took
 * the queueing half away with the reading half, and queueing is the part that
 * most wants doing from wherever you are.
 *
 * The size is read live rather than at mount, and narrowing the window *closes*
 * the desk rather than merely hiding it — a desk still open behind a library is
 * a source held in state that the library can meanwhile delete, and re-widening
 * to find yourself back inside something you left is worse than one extra tap.
 */
export function Study() {
  const isLargeScreen = useIsLargeScreen();
  const [open, setOpen] = useState<StudySource | null>(null);

  useEffect(() => {
    if (!isLargeScreen) setOpen(null);
  }, [isLargeScreen]);

  if (open && isLargeScreen) {
    return (
      <StudyDesk
        sourceId={open.id}
        initial={open}
        onBack={() => setOpen(null)}
      />
    );
  }
  return <StudyLibrary onOpen={setOpen} canOpen={isLargeScreen} />;
}
