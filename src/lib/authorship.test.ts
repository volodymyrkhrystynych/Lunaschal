import { describe, expect, it } from 'vitest';
import fixture from '../../backend/tests/fixtures/text_authorship.json';
import {
  AuthorAligner,
  authoredRuns,
  fieldRuns,
  journalContentFallback,
  type TextAuthor,
} from './authorship';

describe('authoredRuns', () => {
  // The server's own cases, so the web, the phone and backend/authorship.py
  // slice the same characters.
  it.each(fixture.runs)('$name', c => {
    expect(authoredRuns(c.text, c.spans, c.fallback as TextAuthor)).toEqual(
      c.runs
    );
  });
});

describe('fieldRuns', () => {
  it('reads the server spans for one field', () => {
    expect(
      fieldRuns(
        'I went to the market today.',
        {
          content: [
            ['a', 14],
            ['u', 6],
            ['a', 7],
          ],
        },
        'content',
        'u'
      )
    ).toEqual([
      ['a', 'I went to the '],
      ['u', 'market'],
      ['a', ' today.'],
    ]);
  });

  it('treats text the spans no longer fit as a local edit, the user’s', () => {
    expect(
      fieldRuns('edited here', { content: [['a', 3]] }, 'content', 'a')
    ).toEqual([['u', 'edited here']]);
  });

  it('falls back when an older server sent none', () => {
    expect(fieldRuns('words', undefined, 'content', 'a')).toEqual([
      ['a', 'words'],
    ]);
    expect(fieldRuns(null, undefined, 'title', 'a')).toEqual([]);
  });
});

describe('journalContentFallback', () => {
  it('is the model’s once polish changed the transcript', () => {
    expect(
      journalContentFallback({ content: 'So, yeah.', rawContent: 'so yeah' })
    ).toBe('a');
    expect(journalContentFallback({ content: 'typed', rawContent: null })).toBe(
      'u'
    );
    expect(
      journalContentFallback({ content: 'same', rawContent: 'same' })
    ).toBe('u');
  });
});

describe('AuthorAligner', () => {
  // Markdown colouring: each rendered piece, fed in source order, takes the
  // authors of the source characters it was rendered from.
  it.each(fixture.aligned)('$name', c => {
    const aligner = new AuthorAligner(c.source, c.spans, 'u');
    expect(c.rendered.map(piece => aligner.runsFor(piece))).toEqual(c.runs);
  });
});
