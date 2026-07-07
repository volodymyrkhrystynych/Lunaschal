import { describe, expect, it } from 'vitest';
import { parseTagsInput } from './tags';

describe('parseTagsInput', () => {
  it('splits on commas and trims whitespace', () => {
    expect(parseTagsInput(' javascript , python ')).toEqual(['javascript', 'python']);
  });

  it('drops empty segments', () => {
    expect(parseTagsInput('js,,python,')).toEqual(['js', 'python']);
  });

  it('preserves case and duplicates — the backend owns normalization', () => {
    expect(parseTagsInput('JavaScript, javascript')).toEqual(['JavaScript', 'javascript']);
  });

  it('returns an empty array for blank input', () => {
    expect(parseTagsInput('')).toEqual([]);
    expect(parseTagsInput('  , ,')).toEqual([]);
  });
});
