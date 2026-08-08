// Whether the Practice tab's "What this is" panel is expanded.
//
// The panel cannot remember this itself. Both drills are keyed on the snippet
// id (`key={current.id}` in the session), so every new snippet remounts the
// component and takes its local state with it — the panel collapsed again on
// each drill, and re-opening it every single time was the whole annoyance.
// Lifting the answer out here means a remount reads back the choice that was
// last made rather than starting over.
//
// localStorage rather than the `settings` table, like fontSize.ts and
// learningSpeechMode.ts: it is a per-machine ergonomic preference, not
// something worth a round-trip or worth following the user to another machine.
const STORAGE_KEY = 'lunaschal:practiceExplanationOpen';

/** The stored choice, or null when the user has never made one. */
export function getStoredExplanationOpen(): boolean | null {
  // null is deliberately distinct from false: "never toggled" is what lets a
  // graded recall still open the panel on its own the first time, before the
  // user has expressed a preference either way. Collapsing the two would mean
  // shipping the panel closed on the one screen whose payoff is reading it.
  const raw = localStorage.getItem(STORAGE_KEY);
  return raw === null ? null : raw === '1';
}

export function setStoredExplanationOpen(open: boolean): boolean {
  localStorage.setItem(STORAGE_KEY, open ? '1' : '0');
  return open;
}
