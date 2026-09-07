import { expect, it } from 'vitest';
import { issueDateFromFilename } from './newspaperImport';

it('suggests a valid date from common issue filenames', () => {
  expect(issueDateFromFilename('Toronto Star-20260906.pdf')).toBe('2026-09-06');
  expect(issueDateFromFilename('toronto-star-2026-09-06.pdf')).toBe(
    '2026-09-06'
  );
  expect(issueDateFromFilename('2024_02_29.pdf')).toBe('2024-02-29');
});
it('leaves missing, impossible, or ambiguous dates for the user to assign', () => {
  for (const name of [
    'newspaper.pdf',
    '20260230.pdf',
    '20260229.pdf',
    '20260901-to-20260902.pdf',
    '12320260906123.pdf',
  ]) {
    expect(issueDateFromFilename(name)).toBe('');
  }
});
