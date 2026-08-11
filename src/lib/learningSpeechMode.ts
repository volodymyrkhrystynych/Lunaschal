// Speech mode toggle for the Learning review session: when on, the grader
// also produces a short spoken blurb about what was missed, played aloud as
// each result card is shown. Stored in localStorage (like fontSize.ts)
// rather than the `settings` DB table — it's a per-session ergonomic
// preference, not something that should follow the user to every machine.
const STORAGE_KEY = 'lunaschal:learningSpeechMode';

export function getStoredSpeechMode(): boolean {
  return localStorage.getItem(STORAGE_KEY) === '1';
}

export function setStoredSpeechMode(enabled: boolean): boolean {
  localStorage.setItem(STORAGE_KEY, enabled ? '1' : '0');
  return enabled;
}
