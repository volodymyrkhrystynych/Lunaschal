// Generates the auto-managed file-tree block that lives inside the notebook's
// index.md. Pure logic, extracted so it's testable without CodeMirror/jsdom —
// the same split src/lib/notebookVim.ts uses.
//
// index.md is the sole entry point into the notebook (there is no file-tree
// sidebar any more), but nothing used to write to it, so a note you didn't
// hand-link from the index was unreachable. The tree below fixes that without
// taking the file away from you: it occupies a delimited region and everything
// outside that region is preserved byte-for-byte.

/** Markers bounding the generated region. HTML comments so they render as
 * nothing in any markdown preview, and distinctive enough not to collide with
 * something a note legitimately contains. */
export const TREE_START = '<!-- notebook:tree -->';
export const TREE_END = '<!-- /notebook:tree -->';

const HEADING = '# Index';

export interface TreeEntry {
  path: string;
  isDir: boolean;
}

const INDENT = '  ';

/** Strips a trailing .md so links read as page names rather than filenames —
 * every other extension is kept, since for those it's part of the identity. */
function displayName(path: string): string {
  const name = path.slice(path.lastIndexOf('/') + 1);
  return name.endsWith('.md') ? name.slice(0, -3) : name;
}

/** Renders the tree as a nested markdown bullet list of wiki-links.
 *
 * Indentation comes from each entry's own path depth rather than from the order
 * the caller walked in, so a listing that arrives flat still nests correctly.
 * Link targets are root-relative (a leading "/", which resolveWikiLinkPath
 * reads as root-relative) and keep their real extension, so following one lands
 * on exactly the file the walk found — no re-resolution guesswork.
 */
export function renderTree(entries: TreeEntry[]): string {
  if (entries.length === 0) {
    return '_No notes yet — follow a `[[Link]]` with `<CR>` to create the first one._';
  }
  return entries
    .map(entry => {
      const depth = entry.path.split('/').length - 1;
      const pad = INDENT.repeat(depth);
      if (entry.isDir) return `${pad}- ${displayName(entry.path)}/`;
      return `${pad}- [[/${entry.path}|${displayName(entry.path)}]]`;
    })
    .join('\n');
}

function findBlock(content: string): { start: number; end: number } | null {
  const start = content.indexOf(TREE_START);
  if (start === -1) return null;
  const end = content.indexOf(TREE_END, start + TREE_START.length);
  // A start marker with no closing partner is damage we refuse to interpret:
  // guessing that the block runs to the end of the file would delete every note
  // written below it. Treated as "no block", which appends a fresh one and
  // leaves the orphan marker for the user to clear.
  if (end === -1) return null;
  return { start, end: end + TREE_END.length };
}

/** Replaces the managed region of `content` with `body`, appending a fresh
 * region when there isn't one yet. Text outside the markers is untouched. */
export function spliceTreeBlock(content: string, body: string): string {
  const block = `${TREE_START}\n${body}\n${TREE_END}`;
  const found = findBlock(content);
  if (found) {
    return content.slice(0, found.start) + block + content.slice(found.end);
  }
  // A brand-new index.md is created empty, so there is nothing to preserve and
  // a bare bullet list with no heading would look like a bug rather than a page.
  if (!content.trim()) return `${HEADING}\n\n${block}\n`;
  return `${content.replace(/\s*$/, '')}\n\n${block}\n`;
}

/** The index.md content for `entries`, given whatever the file holds now.
 * Returns `current` unchanged when the tree is already up to date, so the
 * caller can skip a pointless write. */
export function buildIndexContent(
  current: string,
  entries: TreeEntry[]
): string {
  return spliceTreeBlock(current, renderTree(entries));
}

/** Entries worth listing: the index page itself is dropped (a link from the
 * index to the index is noise), as are empty directories, which would otherwise
 * show as a bullet leading nowhere. */
export function treeEntriesFor(
  entries: TreeEntry[],
  indexPath: string
): TreeEntry[] {
  const kept = entries.filter(e => e.path !== indexPath);
  const hasChildren = (dir: string) =>
    kept.some(e => !e.isDir && e.path.startsWith(`${dir}/`));
  return kept.filter(e => !e.isDir || hasChildren(e.path));
}
