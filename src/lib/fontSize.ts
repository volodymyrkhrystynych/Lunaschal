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
