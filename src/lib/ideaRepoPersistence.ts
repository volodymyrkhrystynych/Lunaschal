// Remembers which repository the Ideas list is filtered to, for the same
// reload-back-to-default reason as viewPersistence.ts: Lunaschal runs as an
// installed standalone PWA, and a backgrounded webview is re-executed from
// scratch on the next screen-on, wiping React state.
//
// It matters more here than for a view, because the selection is not only a
// filter — IdeaCapture stamps new ideas with it. Losing it silently means the
// next thing you dictate is filed against the default repo instead of the one
// you were working in, and nothing on screen says so.
//
// Deliberately local rather than a settings column: this is "which repo am I
// looking at on *this* device", and the phone and the desktop are routinely in
// different repos. A server-side value would make them fight.
const STORAGE_KEY = 'lunaschal:ideaRepo';

/** The sentinel for "show every repository", stored as-is. */
export const ALL_REPOS = 'all';

export function getStoredIdeaRepo(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    // Private mode, or storage disabled. A forgotten filter is a small loss;
    // a crash on mount is not.
    return null;
  }
}

export function setStoredIdeaRepo(repoId: string | null): void {
  try {
    if (repoId) localStorage.setItem(STORAGE_KEY, repoId);
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Same reasoning as above: never let a storage failure break the list.
  }
}

/**
 * The selection to start from, given what is stored and what exists now.
 *
 * Pure so it can be tested without jsdom, and because the interesting rule is
 * the last one: a stored id whose repo has since been removed must fall back to
 * "all" rather than filtering the list down to nothing. An Ideas tab that looks
 * empty is indistinguishable from an Ideas tab with no ideas.
 */
export function resolveIdeaRepo(
  stored: string | null,
  repoIds: string[]
): string {
  if (!stored || stored === ALL_REPOS) return ALL_REPOS;
  return repoIds.includes(stored) ? stored : ALL_REPOS;
}
