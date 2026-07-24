// Content font size — the reading text in the main panels (chat, journal
// entries, cookbook, etc.). Deliberately stored in localStorage rather than the
// `settings` DB table, since it's a per-screen ergonomic preference (desktop
// vs. the low-DPI Pocket 2), not something that should follow the user to
// every machine that happens to point at the same backend.
const STORAGE_KEY = 'lunaschal:fontSize';

// CSS custom property the content region reads (see App.tsx's <main>). Only the
// content font scales — the chrome (sidebar, header, buttons) stays fixed, so
// bumping the size can't blow out the layout the way scaling the root did.
export const CONTENT_FONT_SIZE_VAR = '--content-font-size';

export const FONT_SIZE_MIN = 12;
export const FONT_SIZE_MAX = 30;
export const FONT_SIZE_DEFAULT = 16;

export const FONT_SIZE_PRESETS = [
  { label: 'Small', px: 14 },
  { label: 'Default', px: FONT_SIZE_DEFAULT },
  { label: 'Large', px: 18 },
  { label: 'X-Large', px: 20 },
  { label: 'XX-Large', px: 22 },
  { label: '3X-Large', px: 26 },
  { label: '4X-Large', px: FONT_SIZE_MAX },
] as const;

function clamp(px: number): number {
  return Math.min(FONT_SIZE_MAX, Math.max(FONT_SIZE_MIN, px));
}

export function getStoredFontSize(): number {
  const raw = localStorage.getItem(STORAGE_KEY);
  const parsed = raw === null ? NaN : Number(raw);
  return Number.isFinite(parsed) ? clamp(parsed) : FONT_SIZE_DEFAULT;
}

/**
 * Sets the content font size via a CSS variable consumed by the main content
 * region. Unlike scaling the root font size, this leaves rem-based chrome
 * (sidebar, buttons, padding) untouched, so only the reading text grows.
 */
export function applyFontSize(px: number): void {
  document.documentElement.style.setProperty(
    CONTENT_FONT_SIZE_VAR,
    `${clamp(px)}px`
  );
}

export function setStoredFontSize(px: number): number {
  const clamped = clamp(px);
  localStorage.setItem(STORAGE_KEY, String(clamped));
  applyFontSize(clamped);
  return clamped;
}

// Reading-pane font sizes (Writing chapter editor, Fanfic reader) — scaled with
// per-view shortcuts without touching the chrome. Until the user sets a per-view
// size, they follow the global content size (so the Settings font control drives
// the chapter/reader text too); the `=`/`-` shortcuts then override per view.
// `defaultPx` may be a getter so the fallback can track the live global size.
function createProseFontSizeStore(
  storageKey: string,
  min: number,
  max: number,
  defaultPx: number | (() => number)
) {
  const clampProse = (px: number) => Math.min(max, Math.max(min, px));
  const resolveDefault = () =>
    typeof defaultPx === 'function' ? defaultPx() : defaultPx;
  return {
    getStored(): number {
      const raw = localStorage.getItem(storageKey);
      const parsed = raw === null ? NaN : Number(raw);
      return Number.isFinite(parsed)
        ? clampProse(parsed)
        : clampProse(resolveDefault());
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
export const CHAPTER_FONT_SIZE_STEP = 1;

// Defaults to the global content size until the user sets a per-chapter size.
const chapterFontSizeStore = createProseFontSizeStore(
  'lunaschal:chapterFontSize',
  CHAPTER_FONT_SIZE_MIN,
  CHAPTER_FONT_SIZE_MAX,
  getStoredFontSize
);
export const getStoredChapterFontSize = chapterFontSizeStore.getStored;
export const setStoredChapterFontSize = chapterFontSizeStore.setStored;

export const LEARNING_FONT_SIZE_MIN = 12;
export const LEARNING_FONT_SIZE_MAX = 32;
export const LEARNING_FONT_SIZE_DEFAULT = 20; // matches the old text-xl card size
export const LEARNING_FONT_SIZE_STEP = 1;

const learningFontSizeStore = createProseFontSizeStore(
  'lunaschal:learningFontSize',
  LEARNING_FONT_SIZE_MIN,
  LEARNING_FONT_SIZE_MAX,
  LEARNING_FONT_SIZE_DEFAULT
);
export const getStoredLearningFontSize = learningFontSizeStore.getStored;
export const setStoredLearningFontSize = learningFontSizeStore.setStored;

export const READING_FONT_SIZE_MIN = 12;
export const READING_FONT_SIZE_MAX = 32;
export const READING_FONT_SIZE_STEP = 1;

// Defaults to the global content size until the user sets a per-fic reading size.
const readingFontSizeStore = createProseFontSizeStore(
  'lunaschal:readingFontSize',
  READING_FONT_SIZE_MIN,
  READING_FONT_SIZE_MAX,
  getStoredFontSize
);
export const getStoredReadingFontSize = readingFontSizeStore.getStored;
export const setStoredReadingFontSize = readingFontSizeStore.setStored;
