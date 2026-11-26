import { describe, expect, it } from 'vitest';
import {
  contrast,
  parseColor,
  readable,
  readableChapterHtml,
} from './chapterColors';

const BLACK = { r: 0, g: 0, b: 0 };
const WHITE = { r: 1, g: 1, b: 1 };

describe('parseColor', () => {
  it('reads the forms a forum post writes', () => {
    const red = { r: 1, g: 0, b: 0 };
    expect(parseColor('#f00')).toEqual(red);
    expect(parseColor('#FF0000')).toEqual(red);
    expect(parseColor('rgb(255, 0, 0)')).toEqual(red);
    expect(parseColor('rgba(255,0,0,0.5)')).toEqual(red);
    expect(parseColor('Red')).toEqual(red);
  });

  it('refuses what is not a colour', () => {
    expect(parseColor('rgba(0,0,0,0)')).toBeNull();
    expect(parseColor('url(x)')).toBeNull();
    expect(parseColor('#12')).toBeNull();
  });
});

describe('readable', () => {
  // Same cases as the iOS reader's ChapterStyleTests.
  it('lightens a colour too dark for the dark page only as far as needed', () => {
    const navy = parseColor('navy')!;
    const onDark = readable(navy, true);
    expect(contrast(onDark, BLACK)).toBeGreaterThanOrEqual(3);
    expect(onDark.b).toBeGreaterThan(onDark.r);
    expect(readable(navy, false)).toEqual(navy);
    const yellow = parseColor('yellow')!;
    expect(contrast(readable(yellow, false), WHITE)).toBeGreaterThanOrEqual(3);
    expect(readable(yellow, true)).toEqual(yellow);
  });
});

describe('readableChapterHtml', () => {
  it('rewrites a dark text colour and leaves a light one alone', () => {
    const html =
      '<span style="color:#000080">navy</span> <span style="color:#ffd700">gold</span>';
    const out = readableChapterHtml(html, true);
    expect(out).not.toContain('#000080');
    expect(out).toContain('style="color:#ffd700"');
    expect(out).toContain('>navy</span>');
  });

  it('keeps the other declarations and a colour paired with its own background', () => {
    const html =
      '<span style="font-weight:bold;color:navy">a</span>' +
      '<span style="background-color:#ffff00;color:#000000">b</span>';
    const out = readableChapterHtml(html, true);
    expect(out).toMatch(/style="font-weight:bold;color:#[0-9a-f]{6}"/);
    expect(out).not.toContain('color:navy');
    expect(out).toContain('style="background-color:#ffff00;color:#000000"');
  });

  it('changes nothing on a page the colour was chosen for', () => {
    const html = '<span style="color:#000080">navy</span>';
    expect(readableChapterHtml(html, false)).toBe(html);
  });

  it('leaves prose that quotes a style attribute untouched', () => {
    // The sanitizer escapes quotes in text, so this is how it arrives.
    const html = '<p>style=&quot;color:#000080&quot;</p>';
    expect(readableChapterHtml(html, true)).toBe(html);
  });
});
