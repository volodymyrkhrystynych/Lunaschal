// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, type SpendingDay } from '@/hooks/api';
import { SpendingCard } from './SpendingCard';

vi.mock('@/hooks/api', () => ({
  api: { spending: { trends: vi.fn() } },
}));

const trends = vi.mocked(api.spending.trends);

function renderCard() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <SpendingCard />
    </QueryClientProvider>
  );
}

const day = (
  date: string,
  totalCents: number,
  byCategory: Record<string, number> = {},
  pendingCount = 0
): SpendingDay => ({ date, totalCents, byCategory, pendingCount });

describe('SpendingCard', () => {
  it('points at the phone before anything has been logged', async () => {
    trends.mockResolvedValue([day('2026-10-10', 0), day('2026-10-11', 0)]);
    renderCard();
    expect(await screen.findByText(/Nothing logged/)).toBeTruthy();
  });

  it('draws a bar per day and totals today, the window and the top categories', async () => {
    trends.mockResolvedValue([
      day('2026-10-10', 4286, { Groceries: 4286 }),
      day('2026-10-11', 450, { Coffee: 450 }, 1),
    ]);
    const { container } = renderCard();
    expect(
      await screen.findByText(/Today: \$4\.50 \+ 1 being read/)
    ).toBeTruthy();
    expect(screen.getByText(/\$47\.36 · 1 not read yet/)).toBeTruthy();
    expect(container.querySelectorAll('svg rect')).toHaveLength(2);
    const rows = screen.getByLabelText('Top categories').querySelectorAll('li');
    expect([...rows].map(r => r.textContent)).toEqual([
      'Groceries$42.86',
      'Coffee$4.50',
    ]);
  });

  it('still shows a window whose only purchase is still being read', async () => {
    trends.mockResolvedValue([day('2026-10-11', 0, {}, 1)]);
    renderCard();
    expect(await screen.findByText(/\$0\.00 · 1 not read yet/)).toBeTruthy();
  });
});
