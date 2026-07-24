import { describe, it, expect } from 'vitest';
import { MOBILE_MAX_WIDTH, MOBILE_QUERY } from './breakpoints';

describe('breakpoints', () => {
  it('caps mobile at 767px so it is the exact complement of Tailwind md (768px)', () => {
    expect(MOBILE_MAX_WIDTH).toBe(767);
  });

  it('builds a max-width media query from the breakpoint', () => {
    expect(MOBILE_QUERY).toBe('(max-width: 767px)');
  });
});
