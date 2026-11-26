/**
 * A forum author picks colours against the forum's white page; navy chosen
 * there vanishes on this app's dark one. These move a colour toward the page's
 * opposite only as far as it takes to be read, keeping its hue. A port of the
 * iOS reader's `ChapterColor.readable` (apple/LunaschalCore/.../ChapterStyle.swift),
 * so both readers lighten the same colour the same amount.
 */

export interface Rgb {
  r: number;
  g: number;
  b: number;
}

const NAMED: Record<string, string> = {
  black: '#000000',
  white: '#ffffff',
  red: '#ff0000',
  green: '#008000',
  blue: '#0000ff',
  yellow: '#ffff00',
  orange: '#ffa500',
  purple: '#800080',
  pink: '#ffc0cb',
  gray: '#808080',
  grey: '#808080',
  silver: '#c0c0c0',
  maroon: '#800000',
  navy: '#000080',
  teal: '#008080',
  olive: '#808000',
  lime: '#00ff00',
  aqua: '#00ffff',
  cyan: '#00ffff',
  fuchsia: '#ff00ff',
  magenta: '#ff00ff',
  brown: '#a52a2a',
  gold: '#ffd700',
  crimson: '#dc143c',
  indigo: '#4b0082',
  violet: '#ee82ee',
  darkred: '#8b0000',
  darkblue: '#00008b',
  darkgreen: '#006400',
  orangered: '#ff4500',
  royalblue: '#4169e1',
  skyblue: '#87ceeb',
  deepskyblue: '#00bfff',
  darkorange: '#ff8c00',
  darkviolet: '#9400d3',
  turquoise: '#40e0d0',
  lightblue: '#add8e6',
};

const clamp = (n: number) => Math.min(1, Math.max(0, n));

/** `#rgb`, `#rrggbb`, `rgb()`/`rgba()` and the common names; null otherwise. */
export function parseColor(raw: string): Rgb | null {
  const value = raw.trim().toLowerCase();
  if (value.startsWith('#')) {
    let hex = value.slice(1);
    if (hex.length === 3)
      hex = hex
        .split('')
        .map(c => c + c)
        .join('');
    if (!/^[0-9a-f]{6}$/.test(hex)) return null;
    const n = parseInt(hex, 16);
    return {
      r: ((n >> 16) & 0xff) / 255,
      g: ((n >> 8) & 0xff) / 255,
      b: (n & 0xff) / 255,
    };
  }
  if (value.startsWith('rgb')) {
    const open = value.indexOf('(');
    const close = value.lastIndexOf(')');
    if (open < 0 || close < open) return null;
    const parts = value
      .slice(open + 1, close)
      .split(/[\s,/]+/)
      .filter(Boolean);
    if (parts.length < 3) return null;
    const channel = (text: string) =>
      text.endsWith('%') ? Number(text.slice(0, -1)) / 100 : Number(text) / 255;
    if (parts.length >= 4 && Number(parts[3].replace('%', '')) === 0)
      return null;
    const [r, g, b] = parts.slice(0, 3).map(channel);
    if ([r, g, b].some(c => Number.isNaN(c))) return null;
    return { r: clamp(r), g: clamp(g), b: clamp(b) };
  }
  return NAMED[value] ? parseColor(NAMED[value]) : null;
}

/** WCAG relative luminance. */
export function luminance({ r, g, b }: Rgb): number {
  const linear = (c: number) =>
    c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b);
}

export function contrast(a: Rgb, b: Rgb): number {
  const [la, lb] = [luminance(a), luminance(b)];
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

export function readable(color: Rgb, onDark: boolean, minimum = 3): Rgb {
  const page = onDark ? { r: 0, g: 0, b: 0 } : { r: 1, g: 1, b: 1 };
  const target = onDark ? 1 : 0;
  let out = color;
  for (let step = 1; contrast(out, page) < minimum && step <= 20; step++) {
    const t = step / 20;
    out = {
      r: color.r + (target - color.r) * t,
      g: color.g + (target - color.g) * t,
      b: color.b + (target - color.b) * t,
    };
  }
  return out;
}

const hex = ({ r, g, b }: Rgb) =>
  '#' +
  [r, g, b]
    .map(c =>
      Math.round(c * 255)
        .toString(16)
        .padStart(2, '0')
    )
    .join('');

// `color:` at the start of a declaration, never the tail of `background-color:`.
const STYLE_ATTR = /\bstyle="([^"]*)"/g;
const COLOR_DECL = /(^|;)(\s*color\s*:\s*)([^;]+)/gi;

/**
 * The chapter's HTML with every inline text colour made readable on this
 * page. A colour sitting on its own background is left as the author paired
 * them. Works on the server's sanitized output, whose `style` attributes are
 * always double-quoted (nh3).
 */
export function readableChapterHtml(html: string, onDark: boolean): string {
  return html.replace(STYLE_ATTR, (attr, decls: string) => {
    if (/(^|;)\s*background(-color)?\s*:/i.test(decls)) return attr;
    const fixed = decls.replace(
      COLOR_DECL,
      (whole, lead: string, prop: string, value: string) => {
        const parsed = parseColor(value.replace(/!important/i, ''));
        return parsed ? lead + prop + hex(readable(parsed, onDark)) : whole;
      }
    );
    return `style="${fixed}"`;
  });
}

/** Whether the reader's page (`--color-bg`) is dark; dark when unknown. */
export function pageIsDark(): boolean {
  if (typeof document === 'undefined') return true;
  const bg = parseColor(
    getComputedStyle(document.documentElement).getPropertyValue('--color-bg')
  );
  return bg ? luminance(bg) < 0.5 : true;
}
