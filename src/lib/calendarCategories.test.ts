import { describe, it, expect } from 'vitest';
import {
  CATEGORY_COLORS,
  categoryRingBoxShadow,
  categoryStripeBackground,
  parseCategoryTags,
} from './calendarCategories';

describe('parseCategoryTags', () => {
  it('parses a JSON array of known categories', () => {
    expect(parseCategoryTags('["work","exercise"]')).toEqual([
      'work',
      'exercise',
    ]);
  });

  it('drops anything outside the closed vocabulary and never throws', () => {
    expect(parseCategoryTags('["work","nonsense"]')).toEqual(['work']);
    expect(parseCategoryTags('not json')).toEqual([]);
    expect(parseCategoryTags('{"work":true}')).toEqual([]);
    expect(parseCategoryTags(null)).toEqual([]);
  });
});

describe('categoryRingBoxShadow', () => {
  it('stacks one widening ring per category', () => {
    expect(categoryRingBoxShadow(['work', 'family'])).toBe(
      `0 0 0 2px ${CATEGORY_COLORS.work}, 0 0 0 5px ${CATEGORY_COLORS.family}`
    );
  });
});

describe('categoryStripeBackground', () => {
  it('fills the whole width with a single category', () => {
    expect(categoryStripeBackground(['work'])).toBe(
      `linear-gradient(to right, ${CATEGORY_COLORS.work} 0%, ${CATEGORY_COLORS.work} 100%)`
    );
  });

  it('divides a constant width into hard-edged stripes', () => {
    // Two stops per color, the second starting exactly where the first ends —
    // that is what makes the boundary a hard edge instead of a blend. The
    // element's width never changes, which is the whole reason the day view's
    // line uses stripes rather than the rings the Journal feed uses.
    const bg = categoryStripeBackground(['work', 'family']);
    expect(bg).toBe(
      `linear-gradient(to right, ${CATEGORY_COLORS.work} 0%, ${CATEGORY_COLORS.work} 50%, ` +
        `${CATEGORY_COLORS.family} 50%, ${CATEGORY_COLORS.family} 100%)`
    );
  });

  it('returns nothing for an unclassified event', () => {
    expect(categoryStripeBackground([])).toBe('');
  });
});
