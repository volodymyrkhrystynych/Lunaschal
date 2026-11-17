// @vitest-environment jsdom
import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AuthoredText } from './AuthoredText';

describe('AuthoredText', () => {
  it('paints the user’s words blue and leaves the model’s the text colour', () => {
    const { container } = render(
      <AuthoredText
        text="I went to the market today."
        authorship={{
          content: [
            ['a', 14],
            ['u', 6],
            ['a', 7],
          ],
        }}
        field="content"
        fallback="u"
      />
    );
    const spans = [...container.querySelectorAll('span')];
    expect(spans.map(s => [s.className, s.textContent])).toEqual([
      ['text-ai', 'I went to the '],
      ['text-user', 'market'],
      ['text-ai', ' today.'],
    ]);
  });

  it('renders nothing for an empty field', () => {
    const { container } = render(
      <AuthoredText text="" authorship={{}} field="title" fallback="a" />
    );
    expect(container.textContent).toBe('');
  });
});
