// Base UI font size — deliberately stored in localStorage rather than the
// `settings` DB table, since it's a per-screen ergonomic preference (desktop
// vs. the low-DPI Pocket 2), not something that should follow the user to
// every machine that happens to point at the same backend.
const STORAGE_KEY = 'lunaschal:fontSize';

export const FONT_SIZE_MIN = 12;
export const FONT_SIZE_MAX = 24;
export const FONT_SIZE_DEFAULT = 16;

export const FONT_SIZE_PRESETS = [
  { label: 'Small', px: 14 },
  { label: 'Default', px: FONT_SIZE_DEFAULT },
  { label: 'Large', px: 18 },
  { label: 'X-Large', px: 20 },
  { label: 'XX-Large', px: 22 },
] as const;

function clamp(px: number): number {
  return Math.min(FONT_SIZE_MAX, Math.max(FONT_SIZE_MIN, px));
}

export function getStoredFontSize(): number {
  const raw = localStorage.getItem(STORAGE_KEY);
  const parsed = raw === null ? NaN : Number(raw);
  return Number.isFinite(parsed) ? clamp(parsed) : FONT_SIZE_DEFAULT;
}

/** Sets the root font size so all rem-based sizing (including Tailwind's) scales with it. */
export function applyFontSize(px: number): void {
  document.documentElement.style.fontSize = `${clamp(px)}px`;
}

export function setStoredFontSize(px: number): number {
  const clamped = clamp(px);
  localStorage.setItem(STORAGE_KEY, String(clamped));
  applyFontSize(clamped);
  return clamped;
}

// Chapter prose font size (Writing view) — independent of the base UI size so
// the editor text can be scaled with shortcuts without touching the chrome.
// Also localStorage: a per-screen ergonomic preference, like the base size.
const CHAPTER_STORAGE_KEY = 'lunaschal:chapterFontSize';

export const CHAPTER_FONT_SIZE_MIN = 12;
export const CHAPTER_FONT_SIZE_MAX = 32;
export const CHAPTER_FONT_SIZE_DEFAULT = 16;
export const CHAPTER_FONT_SIZE_STEP = 1;

function clampChapter(px: number): number {
  return Math.min(CHAPTER_FONT_SIZE_MAX, Math.max(CHAPTER_FONT_SIZE_MIN, px));
}

export function getStoredChapterFontSize(): number {
  const raw = localStorage.getItem(CHAPTER_STORAGE_KEY);
  const parsed = raw === null ? NaN : Number(raw);
  return Number.isFinite(parsed) ? clampChapter(parsed) : CHAPTER_FONT_SIZE_DEFAULT;
}

export function setStoredChapterFontSize(px: number): number {
  const clamped = clampChapter(px);
  localStorage.setItem(CHAPTER_STORAGE_KEY, String(clamped));
  return clamped;
}
