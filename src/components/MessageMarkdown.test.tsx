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

describe('MessageMarkdown images', () => {
  it.each([
    '![seen](https://evil.example/p?q=1)',
    '![seen](//evil.example/p)',
    '![seen](/\\evil.example/p)',
    '![seen](\\\\evil.example/p)',
    '![seen](data:image/png;base64,AAAA)',
    '![seen](blob:https://evil.example/1)',
    '![seen](/api/chat/attachments/1/file)',
    '![seen][r]\n\n[r]: https://evil.example/p',
    '[![seen](https://evil.example/p)](https://ok.example)',
  ])('shows the alt text and loads nothing: %s', content => {
    const { container } = render(<MessageMarkdown content={content} />);
    expect(container.querySelector('img')).toBeNull();
    expect(container.innerHTML).not.toContain('src=');
    expect(container.textContent).toContain('seen');
  });
});

describe('MessageMarkdown authorship', () => {
  it('paints mixed authors through the markdown syntax', () => {
    // "**Buy** milk" typed, ", said aloud" speech-to-text of a recording.
    const { container } = render(
      <MessageMarkdown
        content="**Buy** milk, said aloud"
        authored={{
          authorship: {
            content: [
              ['u', 12],
              ['s', 12, 'R'],
            ],
          },
          field: 'content',
          fallback: 'a',
        }}
      />
    );
    const runs = [...container.querySelectorAll('span[class^="text-"]')].map(
      s => [s.className, s.textContent]
    );
    expect(runs).toEqual([
      ['text-user', 'Buy'],
      ['text-user', ' milk'],
      ['text-speech', ', said aloud'],
    ]);
    expect(container.querySelector('strong')?.textContent).toBe('Buy');
  });

  it('keeps each list item its own author across the breaks between them', () => {
    const { container } = render(
      <MessageMarkdown
        content={'- typed item\n- AI item'}
        authored={{
          authorship: {
            content: [
              ['u', 13],
              ['a', 9],
            ],
          },
          field: 'content',
          fallback: 'a',
        }}
      />
    );
    const items = [...container.querySelectorAll('li')].map(li =>
      [...li.querySelectorAll('span[class^="text-"]')].map(s => [
        s.className,
        s.textContent,
      ])
    );
    expect(items).toEqual([
      [['text-user', 'typed item']],
      [['text-ai', 'AI item']],
    ]);
  });

  it('colours a single-author field as a whole and adds no spans', () => {
    const { container } = render(
      <MessageMarkdown
        content="All **generated**."
        authored={{
          authorship: { content: [['a', 18]] },
          field: 'content',
          fallback: 'u',
        }}
      />
    );
    expect(container.firstElementChild?.className).toContain('text-ai');
    expect(container.querySelectorAll('span[class^="text-"]')).toHaveLength(0);
  });

  it('is unchanged without authorship', () => {
    const { container } = render(<MessageMarkdown content="plain" />);
    expect(container.firstElementChild?.className.trim()).toBe('break-words');
  });
});
