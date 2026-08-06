import { describe, it, expect } from 'vitest';
import { isAtBottom, STICK_THRESHOLD_PX } from './chatScroll';

describe('isAtBottom', () => {
  it('is true when scrolled exactly to the bottom', () => {
    expect(
      isAtBottom({ scrollTop: 400, scrollHeight: 1000, clientHeight: 600 })
    ).toBe(true);
  });

  it('is true within the threshold, for sub-pixel rounding at a real bottom', () => {
    // A fractional-DPI or zoomed display leaves a few pixels of slack at a
    // genuine scroll-bottom; a threshold of 0 would read that as "the user
    // scrolled away" and stop following the reply.
    expect(
      isAtBottom({ scrollTop: 398, scrollHeight: 1000, clientHeight: 600 })
    ).toBe(true);
  });

  it('is false once the user has scrolled up to read', () => {
    expect(
      isAtBottom({ scrollTop: 100, scrollHeight: 1000, clientHeight: 600 })
    ).toBe(false);
  });

  it('is false exactly one pixel past the threshold', () => {
    const scrollTop = 1000 - 600 - STICK_THRESHOLD_PX - 1;
    expect(
      isAtBottom({ scrollTop, scrollHeight: 1000, clientHeight: 600 })
    ).toBe(false);
    expect(
      isAtBottom({
        scrollTop: scrollTop + 1,
        scrollHeight: 1000,
        clientHeight: 600,
      })
    ).toBe(true);
  });

  it('is true for content shorter than the viewport', () => {
    // Nothing to scroll: the transcript should keep following new content.
    expect(
      isAtBottom({ scrollTop: 0, scrollHeight: 200, clientHeight: 600 })
    ).toBe(true);
  });

  it('is true for the zero metrics jsdom reports, so tests still follow content', () => {
    expect(isAtBottom({ scrollTop: 0, scrollHeight: 0, clientHeight: 0 })).toBe(
      true
    );
  });
});
