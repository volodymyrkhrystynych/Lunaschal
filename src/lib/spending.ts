import type { SpendingDay, SpendingEntry } from '../hooks/api';

// Amounts are CAD cents end to end (backend/routes/spending.py); this is the
// one place they become dollars, so rounding happens nowhere else.
const CAD = new Intl.NumberFormat('en-CA', {
  style: 'currency',
  currency: 'CAD',
});

export function formatCents(cents: number): string {
  return CAD.format(cents / 100);
}

// What a purchase is called on its card: where, then what for, then — for a
// receipt nobody has read yet — what it is.
export function spendingTitle(
  entry: Pick<SpendingEntry, 'merchant' | 'category' | 'media'>
): string {
  const parts = [entry.merchant, entry.category].filter(
    (p): p is string => !!p && !!p.trim()
  );
  if (parts.length) return parts.join(' · ');
  return entry.media.some(m => m.kind === 'image') ? 'Receipt' : 'Purchase';
}

// The amount as the card shows it. A missing amount is "reading" while a pass
// is queued and an honest dash once nothing more is coming — never $0.00.
export function spendingAmountLabel(
  entry: Pick<SpendingEntry, 'amountCents' | 'reading'>
): string {
  if (entry.amountCents != null) return formatCents(entry.amountCents);
  return entry.reading ? 'reading…' : '—';
}

// A line typed on the Daily page has nothing to show but its title and amount,
// so it is drawn as one row rather than a card.
export function isCompactSpending(
  entry: Pick<SpendingEntry, 'media' | 'notes'>
): boolean {
  return entry.media.length === 0 && !entry.notes;
}

export interface SpendBar {
  date: string;
  x: number;
  y: number;
  width: number;
  height: number;
  totalCents: number;
  pendingCount: number;
}

// One bar per day, in the order given (oldest first, as /trends returns it).
// A day with spending never draws thinner than 1px, so a $2 coffee beside a
// $900 rent day is still visibly a day something was bought.
export function spendBars(
  days: SpendingDay[],
  width: number,
  height: number,
  gap = 2
): SpendBar[] {
  if (days.length === 0) return [];
  const max = Math.max(0, ...days.map(d => d.totalCents));
  const slot = width / days.length;
  const barWidth = Math.max(1, slot - gap);
  return days.map((d, i) => {
    const raw = max > 0 ? (d.totalCents / max) * height : 0;
    const h = d.totalCents > 0 ? Math.max(1, raw) : 0;
    return {
      date: d.date,
      x: i * slot + (slot - barWidth) / 2,
      y: height - h,
      width: barWidth,
      height: h,
      totalCents: d.totalCents,
      pendingCount: d.pendingCount,
    };
  });
}

// Where the money went across the window, largest first.
export function topCategories(
  days: SpendingDay[],
  limit = 5
): { label: string; cents: number }[] {
  const totals = new Map<string, number>();
  for (const day of days) {
    for (const [label, cents] of Object.entries(day.byCategory)) {
      totals.set(label, (totals.get(label) ?? 0) + cents);
    }
  }
  return [...totals.entries()]
    .map(([label, cents]) => ({ label, cents }))
    .sort((a, b) => b.cents - a.cents || a.label.localeCompare(b.label))
    .slice(0, limit);
}

export function windowTotal(days: SpendingDay[]): number {
  return days.reduce((n, d) => n + d.totalCents, 0);
}
