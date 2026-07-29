// Remembers which fic is open in the Library reader, for the same reload-back-
// to-default reason as viewPersistence.ts. Only the fic id needs storing: the
// backend already tracks lastReadChapterId per fic (kept current on every
// chapter change via Reader's saveProgress call) and Reader resumes there
// whenever it's opened without an explicit chapter.
const STORAGE_KEY = 'lunaschal:openFic';

export interface StoredFicTarget {
  ficId: string;
}

export function getStoredFicTarget(): StoredFicTarget | null {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (
      typeof parsed === 'object' &&
      parsed !== null &&
      typeof (parsed as { ficId?: unknown }).ficId === 'string'
    ) {
      return { ficId: (parsed as { ficId: string }).ficId };
    }
  } catch {
    // fall through to null below
  }
  return null;
}

export function setStoredFicTarget(target: StoredFicTarget | null): void {
  if (target) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(target));
  } else {
    localStorage.removeItem(STORAGE_KEY);
  }
}
