// What a chapter or note editor has to remember between saves to stay honest
// with the other devices writing to the same book (backend/writing/revisioned.py).
//
// Two things here look like they could live in the editor and cannot. The
// revision a save is based on has to be read when the save is *sent*, not when
// it is queued: autosaves made offline all captured the same base, so the
// second to replay would conflict with the first. And a refused save's text has
// to outlive the page, because a queued save can be refused long after the
// editor that made it has gone.

const CONFLICT_PREFIX = 'lunaschal:writingConflict:';

const revisions = new Map<string, number>();

export const documentKey = (kind: 'chapter' | 'note', id: string) =>
  `${kind}:${id}`;

/** The revision to send: the newest this page has seen for the document. */
export function baseRevision(
  key: string,
  captured: number | undefined
): number | undefined {
  const latest = revisions.get(key);
  if (latest === undefined) return captured;
  return captured === undefined ? latest : Math.max(latest, captured);
}

export function rememberRevision(key: string, revision: number | undefined) {
  if (typeof revision === 'number') revisions.set(key, revision);
}

/** After "Load latest" or a restore, the server's number is simply the truth. */
export function resetRevision(key: string, revision: number | undefined) {
  revisions.delete(key);
  rememberRevision(key, revision);
}

export function holdConflict(key: string, text: string) {
  try {
    localStorage.setItem(CONFLICT_PREFIX + key, text);
  } catch {
    // Storage blocked or full: the open editor still has the text on screen.
  }
}

export function heldConflict(key: string): string | null {
  try {
    return localStorage.getItem(CONFLICT_PREFIX + key);
  } catch {
    return null;
  }
}

export function clearConflict(key: string) {
  try {
    localStorage.removeItem(CONFLICT_PREFIX + key);
  } catch {
    // Nothing was held, then.
  }
}
