import { describe, it, expect } from 'vitest';
import {
  diaryPathFor,
  wikiLinkTargetAt,
  resolveWikiLinkPath,
  toggleCheckboxLine,
} from './notebookVim';

describe('diaryPathFor', () => {
  it('formats a mid-day date as diary/YYYY-MM-DD.md', () => {
    expect(diaryPathFor(new Date(2026, 7, 19, 10, 0))).toBe(
      'diary/2026-08-19.md'
    );
  });

  it('zero-pads single-digit month and day', () => {
    expect(diaryPathFor(new Date(2026, 0, 5, 10, 0))).toBe(
      'diary/2026-01-05.md'
    );
  });

  // Mirrors backend/day_boundary.py: the day rolls over at 4am, not midnight.
  it('treats a time before 4am as still belonging to the previous day', () => {
    expect(diaryPathFor(new Date(2026, 7, 19, 2, 0))).toBe(
      'diary/2026-08-18.md'
    );
  });

  it('treats exactly 4am as the start of the new day', () => {
    expect(diaryPathFor(new Date(2026, 7, 19, 4, 0))).toBe(
      'diary/2026-08-19.md'
    );
  });
});

describe('wikiLinkTargetAt', () => {
  it('finds the target when the cursor is inside the brackets', () => {
    const line = 'see [[Some Page]] for details';
    expect(wikiLinkTargetAt(line, 8)).toBe('Some Page');
  });

  it('finds the target when the cursor is at either edge of the link', () => {
    const line = '[[Page]]';
    expect(wikiLinkTargetAt(line, 0)).toBe('Page');
    expect(wikiLinkTargetAt(line, line.length)).toBe('Page');
  });

  it('strips a |Label suffix from the target', () => {
    const line = '[[Target Page|shown text]]';
    expect(wikiLinkTargetAt(line, 3)).toBe('Target Page');
  });

  it('returns null when the cursor is outside any link', () => {
    const line = 'plain text [[Page]] more text';
    expect(wikiLinkTargetAt(line, 2)).toBeNull();
    expect(wikiLinkTargetAt(line, 25)).toBeNull();
  });

  it('returns null when the line has no link at all', () => {
    expect(wikiLinkTargetAt('no links here', 3)).toBeNull();
  });

  it('picks the right link among several on the same line', () => {
    const line = '[[First]] and [[Second]]';
    expect(wikiLinkTargetAt(line, 18)).toBe('Second');
  });
});

describe('resolveWikiLinkPath', () => {
  it('appends .md when the target has no extension', () => {
    expect(resolveWikiLinkPath('Some Page', 'notes.md')).toBe('Some Page.md');
  });

  it('leaves an existing extension alone', () => {
    expect(resolveWikiLinkPath('image.png', 'notes.md')).toBe('image.png');
  });

  it("resolves relative to the current file's directory", () => {
    expect(resolveWikiLinkPath('Sibling', 'diary/2026-08-19.md')).toBe(
      'diary/Sibling.md'
    );
  });

  it('resolves from the root when the current file has no directory', () => {
    expect(resolveWikiLinkPath('Page', 'notes.md')).toBe('Page.md');
  });

  it('treats a leading slash as root-relative, ignoring the current directory', () => {
    expect(resolveWikiLinkPath('/Top Level', 'diary/2026-08-19.md')).toBe(
      'Top Level.md'
    );
  });

  it('honors an explicit sub-path target', () => {
    expect(resolveWikiLinkPath('projects/Notes', 'diary/2026-08-19.md')).toBe(
      'diary/projects/Notes.md'
    );
  });
});

describe('toggleCheckboxLine', () => {
  it('checks an unchecked box', () => {
    expect(toggleCheckboxLine('- [ ] buy milk')).toBe('- [x] buy milk');
  });

  it('unchecks a checked box', () => {
    expect(toggleCheckboxLine('- [x] buy milk')).toBe('- [ ] buy milk');
  });

  it('treats an uppercase X as checked', () => {
    expect(toggleCheckboxLine('- [X] buy milk')).toBe('- [ ] buy milk');
  });

  it('preserves indentation and bullet style', () => {
    expect(toggleCheckboxLine('  * [ ] nested')).toBe('  * [x] nested');
  });

  it('turns a plain list item into an unchecked checkbox item', () => {
    expect(toggleCheckboxLine('- buy milk')).toBe('- [ ] buy milk');
  });

  it('leaves a non-list line unchanged', () => {
    expect(toggleCheckboxLine('just a paragraph')).toBe('just a paragraph');
  });

  it('leaves a blank line unchanged', () => {
    expect(toggleCheckboxLine('')).toBe('');
  });
});
