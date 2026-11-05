import { describe, expect, it } from 'vitest';
import type { SpendingDay, SpendingMedia } from '../hooks/api';
import {
  formatCents,
  isCompactSpending,
  spendBars,
  spendingAmountLabel,
  spendingTitle,
  topCategories,
  windowTotal,
} from './spending';

const day = (
  date: string,
  totalCents: number,
  byCategory: Record<string, number> = {},
  pendingCount = 0
): SpendingDay => ({ date, totalCents, byCategory, pendingCount });

const photo: SpendingMedia = {
  id: 'm1',
  kind: 'image',
  position: 0,
  url: '/api/spending/media/m1',
};

describe('formatCents', () => {
  it('formats whole cents as CAD', () => {
    expect(formatCents(4286)).toBe('$42.86');
    expect(formatCents(5)).toBe('$0.05');
  });
});

describe('spendingTitle', () => {
  it('joins merchant and category', () => {
    expect(
      spendingTitle({ merchant: 'Loblaws', category: 'Groceries', media: [] })
    ).toBe('Loblaws · Groceries');
  });

  it('names an unread receipt as one', () => {
    expect(
      spendingTitle({ merchant: null, category: ' ', media: [photo] })
    ).toBe('Receipt');
    expect(spendingTitle({ merchant: null, category: null, media: [] })).toBe(
      'Purchase'
    );
  });
});

describe('spendingAmountLabel', () => {
  it('never shows a missing amount as zero', () => {
    expect(spendingAmountLabel({ amountCents: null, reading: true })).toBe(
      'reading…'
    );
    expect(spendingAmountLabel({ amountCents: null, reading: false })).toBe(
      '—'
    );
    expect(spendingAmountLabel({ amountCents: 450, reading: false })).toBe(
      '$4.50'
    );
  });
});

describe('isCompactSpending', () => {
  it('is a single row only without media or notes', () => {
    expect(isCompactSpending({ media: [], notes: null })).toBe(true);
    expect(isCompactSpending({ media: [photo], notes: null })).toBe(false);
    expect(isCompactSpending({ media: [], notes: 'with Sam' })).toBe(false);
  });
});

describe('spendBars', () => {
  it('scales to the largest day and keeps small days visible', () => {
    const bars = spendBars(
      [day('a', 0), day('b', 200), day('c', 100000)],
      300,
      100,
      0
    );
    expect(bars.map(b => b.height)).toEqual([0, 1, 100]);
    expect(bars.map(b => b.x)).toEqual([0, 100, 200]);
    expect(bars[2].y).toBe(0);
    expect(bars[0].y).toBe(100);
  });

  it('draws nothing tall for an all-empty window', () => {
    expect(
      spendBars([day('a', 0), day('b', 0)], 100, 50).every(b => b.height === 0)
    ).toBe(true);
    expect(spendBars([], 100, 50)).toEqual([]);
  });
});

describe('topCategories', () => {
  it('sums across days and sorts largest first', () => {
    const days = [
      day('a', 500, { Coffee: 450, Snacks: 50 }),
      day('b', 5000, { Groceries: 4000, Coffee: 1000 }),
    ];
    expect(topCategories(days, 2)).toEqual([
      { label: 'Groceries', cents: 4000 },
      { label: 'Coffee', cents: 1450 },
    ]);
    expect(windowTotal(days)).toBe(5500);
  });
});
