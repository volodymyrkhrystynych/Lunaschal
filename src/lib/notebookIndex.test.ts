import { describe, it, expect } from 'vitest';
import {
  TREE_START,
  TREE_END,
  renderTree,
  spliceTreeBlock,
  buildIndexContent,
  treeEntriesFor,
} from './notebookIndex';

const file = (path: string) => ({ path, isDir: false });
const dir = (path: string) => ({ path, isDir: true });

describe('renderTree', () => {
  it('links files as root-relative wiki-links labelled without the .md', () => {
    expect(renderTree([file('scratch.md')])).toBe('- [[/scratch.md|scratch]]');
  });

  it('keeps a non-markdown extension in the label', () => {
    expect(renderTree([file('budget.csv')])).toBe(
      '- [[/budget.csv|budget.csv]]'
    );
  });

  it('indents by path depth and marks directories with a trailing slash', () => {
    expect(
      renderTree([
        dir('diary'),
        file('diary/2026-08-20.md'),
        file('scratch.md'),
      ])
    ).toBe(
      [
        '- diary/',
        '  - [[/diary/2026-08-20.md|2026-08-20]]',
        '- [[/scratch.md|scratch]]',
      ].join('\n')
    );
  });

  it('nests deeply without depending on walk order', () => {
    // Deliberately handed in flat/unsorted: indentation comes from the paths.
    expect(renderTree([file('a/b/c.md'), dir('a'), dir('a/b')])).toBe(
      ['    - [[/a/b/c.md|c]]', '- a/', '  - b/'].join('\n')
    );
  });

  it('explains itself rather than rendering an empty list', () => {
    expect(renderTree([])).toContain('No notes yet');
  });
});

describe('spliceTreeBlock', () => {
  it('seeds an empty index with a heading and the block', () => {
    expect(spliceTreeBlock('', '- [[/a.md|a]]')).toBe(
      `# Index\n\n${TREE_START}\n- [[/a.md|a]]\n${TREE_END}\n`
    );
  });

  it('appends a block to hand-written content, preserving it', () => {
    expect(spliceTreeBlock('# My notes\n\nSome prose.', '- x')).toBe(
      `# My notes\n\nSome prose.\n\n${TREE_START}\n- x\n${TREE_END}\n`
    );
  });

  it('replaces only the managed region, keeping text above and below', () => {
    const current = [
      '# Index',
      '',
      'Pinned: [[/todo.md|todo]]',
      '',
      TREE_START,
      '- [[/old.md|old]]',
      TREE_END,
      '',
      'Footer notes I wrote.',
    ].join('\n');

    const next = spliceTreeBlock(current, '- [[/new.md|new]]');

    expect(next).toContain('Pinned: [[/todo.md|todo]]');
    expect(next).toContain('Footer notes I wrote.');
    expect(next).toContain('- [[/new.md|new]]');
    expect(next).not.toContain('- [[/old.md|old]]');
  });

  it('is idempotent — re-splicing the same body changes nothing', () => {
    const once = spliceTreeBlock('# Index\n\nprose', '- a');
    expect(spliceTreeBlock(once, '- a')).toBe(once);
  });

  it('refuses to interpret an unclosed marker, so notes below it survive', () => {
    // Guessing the block ran to end-of-file would delete "Precious notes".
    const damaged = `# Index\n\n${TREE_START}\n- stale\n\nPrecious notes.`;
    const next = spliceTreeBlock(damaged, '- fresh');
    expect(next).toContain('Precious notes.');
    expect(next).toContain(`${TREE_START}\n- fresh\n${TREE_END}`);
  });

  it('ignores an end marker that precedes the start marker', () => {
    const weird = `${TREE_END}\ntext\n${TREE_START}\nbody`;
    expect(spliceTreeBlock(weird, '- a')).toContain('text');
  });
});

describe('buildIndexContent', () => {
  it('returns an unchanged string when the tree already matches', () => {
    const entries = [file('a.md')];
    const first = buildIndexContent('', entries);
    expect(buildIndexContent(first, entries)).toBe(first);
  });

  it('picks up a newly added file', () => {
    const first = buildIndexContent('', [file('a.md')]);
    const second = buildIndexContent(first, [file('a.md'), file('b.md')]);
    expect(second).toContain('[[/b.md|b]]');
  });
});

describe('treeEntriesFor', () => {
  it('drops the index page itself', () => {
    expect(
      treeEntriesFor([file('index.md'), file('a.md')], 'index.md')
    ).toEqual([file('a.md')]);
  });

  it('drops directories holding no files', () => {
    expect(
      treeEntriesFor([dir('empty'), dir('full'), file('full/a.md')], 'index.md')
    ).toEqual([dir('full'), file('full/a.md')]);
  });

  it('keeps a directory whose only files are nested deeper', () => {
    const entries = [dir('a'), dir('a/b'), file('a/b/c.md')];
    expect(treeEntriesFor(entries, 'index.md')).toEqual(entries);
  });

  it('drops a directory whose only content is the index page', () => {
    expect(
      treeEntriesFor([dir('d'), file('d/index.md')], 'd/index.md')
    ).toEqual([]);
  });
});
