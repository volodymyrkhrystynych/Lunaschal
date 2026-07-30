// Mid-workout draft persistence for the workout log.
//
// Logging happens live on a phone, mid-session. A phone browser reloads a
// backgrounded tab from scratch (screen lock, switching to a music app between
// sets, a notification), which silently wipes a plain textarea — so the form is
// mirrored to localStorage on every change and restored on load. localStorage
// rather than the server on purpose: no round-trip, so it survives losing
// signal in a basement gym. See docs/lifestyle-tab.md §2.
//
// Lunaschal is an installed PWA, which gets killed less eagerly than a plain
// tab, but "less eagerly" is not "never" — this is the actual fix, not the PWA.
const STORAGE_KEY = 'lunaschal:workoutDraft';

/** Debounce for the save; short enough that a kill mid-set loses at most a word. */
export const DRAFT_SAVE_DELAY_MS = 300;

export interface WorkoutDraft {
  rawText: string;
  locationType: string | null;
  durationMinutes: string;
  intensityRating: string;
  notes: string;
}

export const EMPTY_DRAFT: WorkoutDraft = {
  rawText: '',
  locationType: null,
  durationMinutes: '',
  intensityRating: '',
  notes: '',
};

export function isDraftEmpty(draft: WorkoutDraft): boolean {
  return (
    !draft.rawText.trim() &&
    !draft.locationType &&
    !draft.durationMinutes.trim() &&
    !draft.intensityRating.trim() &&
    !draft.notes.trim()
  );
}

function str(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

/** Coerce whatever is in storage into a usable draft; unknown shapes are
 *  ignored field by field rather than throwing the whole draft away. */
export function parseDraft(raw: string | null): WorkoutDraft | null {
  if (!raw) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof parsed !== 'object' || parsed === null) return null;
  const d = parsed as Record<string, unknown>;
  const draft: WorkoutDraft = {
    rawText: str(d.rawText),
    locationType: typeof d.locationType === 'string' ? d.locationType : null,
    durationMinutes: str(d.durationMinutes),
    intensityRating: str(d.intensityRating),
    notes: str(d.notes),
  };
  return isDraftEmpty(draft) ? null : draft;
}

export function loadWorkoutDraft(): WorkoutDraft | null {
  try {
    return parseDraft(localStorage.getItem(STORAGE_KEY));
  } catch {
    return null;
  }
}

export function saveWorkoutDraft(draft: WorkoutDraft): void {
  try {
    if (isDraftEmpty(draft)) {
      localStorage.removeItem(STORAGE_KEY);
      return;
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(draft));
  } catch {
    // A full or blocked storage quota must never break logging a workout.
  }
}

export function clearWorkoutDraft(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
}
