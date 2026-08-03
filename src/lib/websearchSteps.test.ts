import { describe, it, expect } from 'vitest';
import { stepLabel, parseWebSearchMeta } from './websearchSteps';

describe('stepLabel', () => {
  it('describes a successful search with its result count', () => {
    expect(
      stepLabel({ tool: 'web_search', arg: 'fsrs', ok: true, count: 3 })
    ).toBe('Searched the web for "fsrs" — 3 results');
  });

  it('describes a search with zero results', () => {
    expect(
      stepLabel({ tool: 'web_search', arg: 'fsrs', ok: true, count: 0 })
    ).toBe('Searched the web for "fsrs" — 0 results');
  });

  it('describes an unavailable search', () => {
    expect(
      stepLabel({
        tool: 'web_search',
        arg: 'fsrs',
        ok: false,
        error: 'no provider',
      })
    ).toBe('Web search unavailable: no provider');
  });

  it('describes an unavailable search without an error message', () => {
    expect(stepLabel({ tool: 'web_search', arg: 'fsrs', ok: false })).toBe(
      'Web search unavailable'
    );
  });

  it('describes a successful fetch, preferring the page title over the url', () => {
    expect(
      stepLabel({
        tool: 'web_fetch',
        arg: 'https://ex.com',
        ok: true,
        title: 'Example',
      })
    ).toBe('Read Example');
  });

  it('falls back to the url when a fetched page has no title', () => {
    expect(
      stepLabel({ tool: 'web_fetch', arg: 'https://ex.com', ok: true })
    ).toBe('Read https://ex.com');
  });

  it('describes a failed fetch', () => {
    expect(
      stepLabel({ tool: 'web_fetch', arg: 'https://ex.com', ok: false })
    ).toBe('Could not read https://ex.com');
  });

  it('falls back to a generic label for an unknown tool', () => {
    expect(stepLabel({ tool: 'mystery_tool' })).toBe('Ran mystery_tool');
  });

  it('falls back to "Thinking" when there is no tool at all', () => {
    expect(stepLabel({})).toBe('Thinking');
  });
});

describe('parseWebSearchMeta', () => {
  it('returns empty arrays for null/undefined metadata', () => {
    expect(parseWebSearchMeta(null)).toEqual({ steps: [], sources: [] });
    expect(parseWebSearchMeta(undefined)).toEqual({ steps: [], sources: [] });
    expect(parseWebSearchMeta('')).toEqual({ steps: [], sources: [] });
  });

  it('returns empty arrays for malformed JSON rather than throwing', () => {
    expect(parseWebSearchMeta('not json')).toEqual({ steps: [], sources: [] });
  });

  it('returns empty arrays for metadata with no steps/sources (e.g. a break marker)', () => {
    expect(parseWebSearchMeta(JSON.stringify({ break: true }))).toEqual({
      steps: [],
      sources: [],
    });
  });

  it('parses steps and sources from a websearch assistant message', () => {
    const meta = JSON.stringify({
      steps: [{ tool: 'web_search', ok: true, count: 1 }],
      sources: [{ url: 'https://ex.com', title: 'Example' }],
    });
    expect(parseWebSearchMeta(meta)).toEqual({
      steps: [{ tool: 'web_search', ok: true, count: 1 }],
      sources: [{ url: 'https://ex.com', title: 'Example' }],
    });
  });
});
