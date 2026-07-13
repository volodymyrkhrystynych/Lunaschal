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

// Reading-pane font sizes (Writing chapter editor, Fanfic reader) — each
// independent of the base UI size so prose can be scaled with shortcuts
// without touching the chrome. Also localStorage: a per-screen ergonomic
// preference, like the base size.
function createProseFontSizeStore(storageKey: string, min: number, max: number, defaultPx: number) {
  const clampProse = (px: number) => Math.min(max, Math.max(min, px));
  return {
    getStored(): number {
      const raw = localStorage.getItem(storageKey);
      const parsed = raw === null ? NaN : Number(raw);
      return Number.isFinite(parsed) ? clampProse(parsed) : defaultPx;
    },
    setStored(px: number): number {
      const clamped = clampProse(px);
      localStorage.setItem(storageKey, String(clamped));
      return clamped;
    },
  };
}

export const CHAPTER_FONT_SIZE_MIN = 12;
export const CHAPTER_FONT_SIZE_MAX = 32;
export const CHAPTER_FONT_SIZE_DEFAULT = 16;
export const CHAPTER_FONT_SIZE_STEP = 1;

const chapterFontSizeStore = createProseFontSizeStore(
  'lunaschal:chapterFontSize', CHAPTER_FONT_SIZE_MIN, CHAPTER_FONT_SIZE_MAX, CHAPTER_FONT_SIZE_DEFAULT,
);
export const getStoredChapterFontSize = chapterFontSizeStore.getStored;
export const setStoredChapterFontSize = chapterFontSizeStore.setStored;

export const READING_FONT_SIZE_MIN = 12;
export const READING_FONT_SIZE_MAX = 32;
export const READING_FONT_SIZE_DEFAULT = 17; // matches .fanfic-prose's 1.05rem default
export const READING_FONT_SIZE_STEP = 1;

const readingFontSizeStore = createProseFontSizeStore(
  'lunaschal:readingFontSize', READING_FONT_SIZE_MIN, READING_FONT_SIZE_MAX, READING_FONT_SIZE_DEFAULT,
);
export const getStoredReadingFontSize = readingFontSizeStore.getStored;
export const setStoredReadingFontSize = readingFontSizeStore.setStored;
