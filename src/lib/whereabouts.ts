import type { Whereabouts } from '../hooks/api';

/** "7h 20m", "45m", "3h". Under a minute rounds to nothing worth saying. */
export function formatStay(seconds: number): string {
  const minutes = Math.round(seconds / 60);
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (!h) return `${m}m`;
  return m ? `${h}h ${m}m` : `${h}h`;
}

function clock(iso: string): string {
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

/** The map card's one-line answer to "where was the day spent": each Work by
 *  time there, then home — "home from 18:40" while still there, otherwise
 *  the time spent. Null when nothing matched a Home or Work. */
export function whereaboutsLine(
  w: Whereabouts | null | undefined
): string | null {
  if (!w) return null;
  const parts = w.work
    .filter(x => x.seconds >= 60)
    .map(x => `${formatStay(x.seconds)} at ${x.name}`);
  if (w.home) {
    if (w.home.here && w.home.lastArrival)
      parts.push(`home from ${clock(w.home.lastArrival)}`);
    else if (w.home.seconds >= 60)
      parts.push(`${formatStay(w.home.seconds)} at home`);
  }
  return parts.length ? parts.join(' · ') : null;
}
