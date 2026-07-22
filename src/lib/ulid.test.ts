import { describe, it, expect } from 'vitest';
import { ulid } from './ulid';

const CROCKFORD = /^[0-9A-HJKMNP-TV-Z]{26}$/;

describe('ulid', () => {
  it('produces a 26-char Crockford base32 string', () => {
    const id = ulid();
    expect(id).toHaveLength(26);
    expect(id).toMatch(CROCKFORD);
  });

  it('is unique across many calls at the same instant', () => {
    const t = Date.now();
    const ids = new Set(Array.from({ length: 1000 }, () => ulid(t)));
    expect(ids.size).toBe(1000);
  });

  it('sorts lexicographically by timestamp', () => {
    const earlier = ulid(1000);
    const later = ulid(2000);
    expect(earlier < later).toBe(true);
  });

  it('encodes the timestamp in the first 10 chars deterministically', () => {
    const prefix = (id: string) => id.slice(0, 10);
    expect(prefix(ulid(0))).toBe('0000000000');
    // Same seed time => same time prefix regardless of random suffix.
    expect(prefix(ulid(123456789))).toBe(prefix(ulid(123456789)));
  });
});
