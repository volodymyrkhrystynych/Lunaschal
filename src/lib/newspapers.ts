// Pure date-math helpers for the Newspapers front-page archive view.
// Dates are plain 'YYYY-MM-DD' strings. Once anchored, shifting by whole
// days is parsed as UTC midnight so the math isn't skewed by the local
// timezone offset. The anchor itself ("today's paper") is the 4am day, as
// everywhere else in the app and as the backend's `scheduler.paper_day` has
// it: from midnight to 4am it is still the previous date's paper.
import { DAY_ROLLOVER_HOUR, localDayKey } from './dates';

/** The date of today's paper: the 4am day containing `now`. */
export function paperDay(now: Date = new Date()): string {
  return localDayKey(now);
}

/** Whether today's paper may be downloaded. From midnight to 4am the 4am day
 * is still yesterday's, and the new date's edition belongs to a day that
 * hasn't started, so the server refuses it. */
export function downloadWindowOpen(now: Date = new Date()): boolean {
  return now.getHours() >= DAY_ROLLOVER_HOUR;
}

export function shiftDateISO(date: string, days: number): string {
  const d = new Date(`${date}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().split('T')[0];
}

export function isFutureDate(date: string, now: Date = new Date()): boolean {
  return date > paperDay(now);
}

// Used to flag the sidebar when a day's front pages haven't all synced yet.
// Any missing paper counts — a partial sync (one paper failed to scrape) is
// still worth surfacing, not just a fully-missed day.
export function hasMissingEditions(
  pages: { imageUrl: string | null }[]
): boolean {
  return pages.some(page => page.imageUrl == null);
}

/** How long an issue load may go without a byte before the reader says so. */
export const ISSUE_LOAD_STALL_MS = 20_000;

export interface IssueLoadProgress {
  loaded: number;
  /** 0 when the server sent no length. */
  total: number;
  pdfDone: boolean;
  markupDone: boolean;
  stalled: boolean;
}

function mb(bytes: number): string {
  return (bytes / 1_000_000).toFixed(1);
}

/**
 * The reader's status line while an issue opens. A bare "Loading…" made a
 * slow transfer, a request that never answered and a stuck markup fetch look
 * identical — on the iPad, forever — so each half says where it is.
 */
export function issueLoadStatus(p: IssueLoadProgress): string {
  if (!p.pdfDone) {
    if (p.loaded === 0) {
      return p.stalled
        ? 'No data from the server yet. Check the connection.'
        : 'Loading issue…';
    }
    const amount = p.total
      ? `${mb(Math.min(p.loaded, p.total))} of ${mb(p.total)} MB`
      : `${mb(p.loaded)} MB`;
    return p.stalled
      ? `Loading stalled at ${amount}. Check the connection.`
      : `Loading issue… ${amount}`;
  }
  if (!p.markupDone) {
    return p.stalled
      ? 'Issue loaded; still waiting for saved markup from the server.'
      : 'Loading markup…';
  }
  return 'Saved';
}
