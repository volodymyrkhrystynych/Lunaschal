import { useState } from 'react';
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
 * Only mounted on a large screen (`largeOnly` in Sidebar's navItems); see
 * src/lib/navVisibility.ts for why that is not the same gate as Piano's.
 */
export function Study() {
  const [open, setOpen] = useState<StudySource | null>(null);

  if (open) {
    return (
      <StudyDesk
        sourceId={open.id}
        initial={open}
        onBack={() => setOpen(null)}
      />
    );
  }
  return <StudyLibrary onOpen={setOpen} />;
}
