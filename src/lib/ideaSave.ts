// The save state of the idea editor, kept pure so it can be tested in the node
// environment (see the testing note in CLAUDE.md).
//
// The editor used to compare what was on screen against the *query cache* and
// call that "dirty". Two things went wrong with that, and both were visible:
//
//  1. On the render where the fetched idea first arrives, the hydration effect
//     has set state but the autosave effect still closes over the old, empty
//     fields — so it declared a difference, flipped to "Saving…", and scheduled
//     a save of an empty title. The next render matched and returned early
//     *before* clearing the flag, so the pane sat on "Saving…" forever with no
//     request in flight. That is the "saving when nothing is changing" spiral.
//  2. The server legitimately changes the row underneath: the background pass
//     that names a captured idea writes `title` seconds after it was created.
//     Compared against the cache, that reads as the user having unsaved work.
//
// So the editor tracks a *baseline* — the last text known to be on the server —
// separately from the draft on screen. Dirty is draft ≠ baseline, which is a
// fact about what has been typed, and a refetch can move the baseline without
// ever implying the user typed something.

export interface IdeaDraft {
  title: string;
  body: string;
}

/**
 * `saved` — draft and server agree, nothing to do.
 * `dirty` — edited, waiting out the debounce. Local only.
 * `saving` — a request is genuinely in flight.
 * `error` — the last save failed; the draft is still only local.
 */
export type SaveState = 'saved' | 'dirty' | 'saving' | 'error';

export function isDirty(draft: IdeaDraft, baseline: IdeaDraft): boolean {
  return draft.title !== baseline.title || draft.body !== baseline.body;
}

/**
 * What the save state is, given the three facts the component knows. Order
 * matters: an in-flight request is `saving` even though the draft it is
 * carrying is by definition still different from the baseline, and a failure is
 * only interesting while there is still something unsaved to lose.
 */
export function saveState(facts: {
  dirty: boolean;
  inFlight: boolean;
  failed: boolean;
}): SaveState {
  if (facts.inFlight) return 'saving';
  if (facts.failed && facts.dirty) return 'error';
  if (facts.dirty) return 'dirty';
  return 'saved';
}

/**
 * The label shown in the header. Each one says where the text currently lives,
 * because that is the actual question: "Unsaved" means this exists only in this
 * browser, and closing the tab loses it.
 */
export function saveLabel(state: SaveState): string {
  switch (state) {
    case 'saving':
      return 'Saving…';
    case 'dirty':
      return 'Unsaved';
    case 'error':
      return 'Save failed';
    default:
      return 'Saved';
  }
}

/** Tailwind classes per state — muted when there is nothing to act on. */
export function saveClasses(state: SaveState): string {
  switch (state) {
    case 'saving':
      return 'text-[var(--color-primary)]';
    case 'dirty':
      return 'text-amber-300';
    case 'error':
      return 'text-red-400';
    default:
      return 'text-[var(--color-text-muted)]';
  }
}

/** Which fields to send. Sending only what changed keeps a title written by
 *  the background naming pass from being echoed back by a body-only edit. */
export function changedFields(
  draft: IdeaDraft,
  baseline: IdeaDraft
): { title?: string; content?: string } {
  const out: { title?: string; content?: string } = {};
  if (draft.title !== baseline.title) out.title = draft.title;
  if (draft.body !== baseline.body) out.content = draft.body;
  return out;
}

export interface ServerIdea {
  title: string;
  content: string;
  rawContent: string;
}

/** What the server's copy of the text is, in draft terms. `content` is the
 *  AI-cleaned prose and `rawContent` the fallback while it is unset — the same
 *  precedence the detail pane has always rendered. */
export function serverDraft(idea: ServerIdea): IdeaDraft {
  return { title: idea.title, body: idea.content || idea.rawContent };
}

/**
 * Fold a freshly fetched row into the editor.
 *
 * Per field: an untouched one adopts whatever the server now says (this is how
 * an auto-generated title appears in the box without a reload), while an edited
 * one is left alone and its baseline is *still* advanced — so a background
 * write to the other field can never resurrect as a phantom local change.
 */
export function mergeServerIdea(
  draft: IdeaDraft,
  baseline: IdeaDraft,
  idea: ServerIdea
): { draft: IdeaDraft; baseline: IdeaDraft } {
  const server = serverDraft(idea);
  return {
    draft: {
      title: draft.title === baseline.title ? server.title : draft.title,
      body: draft.body === baseline.body ? server.body : draft.body,
    },
    baseline: server,
  };
}
