// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { MessageMarkdown } from './MessageMarkdown';

// jsdom doesn't resolve em -> px, so we can't assert computed sizes. Instead we
// guard the regression at the unit level: fenced code blocks and tables must
// use an em-relative font size so they scale with an ambient font-size (e.g.
// the Learning card's font-size shortcut) rather than a fixed rem (`text-xs`).
describe('MessageMarkdown font scaling', () => {
  it('renders fenced code blocks with an em-relative size, not fixed text-xs', () => {
    const { container } = render(
      <MessageMarkdown content={'```js\nconst x = 1;\n```'} />
    );
    const pre = container.querySelector('pre');
    expect(pre).not.toBeNull();
    expect(pre!.className).toContain('text-[0.75em]');
    expect(pre!.className).not.toContain('text-xs');
  });

  it('renders inline code with an em-relative size', () => {
    const { container } = render(
      <MessageMarkdown content={'use `foo()` here'} />
    );
    const code = container.querySelector('p code');
    expect(code).not.toBeNull();
    expect(code!.className).toContain('text-[0.85em]');
  });

  it('renders tables with an em-relative size, not fixed text-xs', () => {
    const { container } = render(
      <MessageMarkdown content={'| a | b |\n| - | - |\n| 1 | 2 |'} />
    );
    const table = container.querySelector('table');
    expect(table).not.toBeNull();
    expect(table!.className).toContain('text-[0.75em]');
    expect(table!.className).not.toContain('text-xs');
  });
});
