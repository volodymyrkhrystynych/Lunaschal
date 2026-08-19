// Pure logic behind Notebook's vimwiki-style editor customizations (diary
// jump, wiki-link resolution, checkbox toggling) — extracted so it's
// testable without spinning up CodeMirror/jsdom.

const DIARY_DIR = 'diary';

// Mirrors backend/day_boundary.py's app-wide 4am day boundary: a diary note
// jumped to at 2am still belongs to the day that started the previous
// morning, the same as a task, workout, or chat message logged that hour —
// and it's what lets the backend's promotion scheduler treat a diary file's
// date key as "that app-day's note" consistently with everything else.
const DAY_ROLLOVER_HOUR = 4;

/** diary/YYYY-MM-DD.md for the given local date (defaults to now), using the
 * app's 4am-rollover day boundary rather than the plain calendar date. */
export function diaryPathFor(date: Date = new Date()): string {
  const rolled = new Date(date.getTime() - DAY_ROLLOVER_HOUR * 60 * 60 * 1000);
  const y = rolled.getFullYear();
  const m = String(rolled.getMonth() + 1).padStart(2, '0');
  const d = String(rolled.getDate()).padStart(2, '0');
  return `${DIARY_DIR}/${y}-${m}-${d}.md`;
}

const WIKI_LINK_RE = /\[\[([^\]|]+)(?:\|[^\]]+)?\]\]/g;

/** Finds a [[Target]] (or [[Target|Label]]) span containing column `col` on
 * `line`, returning its target text, or null if the cursor isn't inside one. */
export function wikiLinkTargetAt(line: string, col: number): string | null {
  WIKI_LINK_RE.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = WIKI_LINK_RE.exec(line))) {
    const start = match.index;
    const end = start + match[0].length;
    if (col >= start && col <= end) return match[1].trim();
  }
  return null;
}

/** Resolves a wiki-link target to a notebook file path, relative to the
 * directory of the file the link appears in — a leading "/" is root-relative,
 * matching vimwiki's default single-wiki link resolution. Appends .md when
 * the target has no extension of its own. */
export function resolveWikiLinkPath(
  target: string,
  currentFilePath: string
): string {
  let name = target.trim();
  if (!/\.[a-zA-Z0-9]+$/.test(name)) name += '.md';
  if (name.startsWith('/')) return name.slice(1);
  const dir = currentFilePath.includes('/')
    ? currentFilePath.slice(0, currentFilePath.lastIndexOf('/') + 1)
    : '';
  return dir + name;
}

const CHECKBOX_RE = /^(\s*[-*+]\s+)\[([ xX])\](.*)$/;
const PLAIN_LIST_RE = /^(\s*[-*+]\s+)(.*)$/;

/** Toggles a "- [ ]"/"- [x]" checkbox line, or turns a plain "- item" list
 * line into an unchecked checkbox item. Lines that are neither are returned
 * unchanged. */
export function toggleCheckboxLine(line: string): string {
  const checkboxMatch = line.match(CHECKBOX_RE);
  if (checkboxMatch) {
    const [, bullet, state, rest] = checkboxMatch;
    const next = state === ' ' ? 'x' : ' ';
    return `${bullet}[${next}]${rest}`;
  }
  const listMatch = line.match(PLAIN_LIST_RE);
  if (listMatch) {
    const [, bullet, rest] = listMatch;
    return `${bullet}[ ] ${rest}`;
  }
  return line;
}
