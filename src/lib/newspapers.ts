// Pure date-math helpers for the Newspapers front-page archive view.
// Dates are plain 'YYYY-MM-DD' strings. Once anchored, shifting by whole
// days is parsed as UTC midnight so the math isn't skewed by the local
// timezone offset — but the anchor itself ("today") must come from the
// viewer's local calendar date, matching the backend's `date.today()`
// (also local). Using `Date#toISOString()` for the anchor would read the
// *UTC* calendar date instead, which drifts a day off local for several
// hours around midnight depending on the timezone offset.

export function todayISO(now: Date = new Date()): string {
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

export function shiftDateISO(date: string, days: number): string {
  const d = new Date(`${date}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().split('T')[0];
}

export function isFutureDate(date: string, now: Date = new Date()): boolean {
  return date > todayISO(now);
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
