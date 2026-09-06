// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { CaloriesCard } from './CaloriesCard';

vi.mock('@/hooks/api', () => ({
  api: {
    lifestyle: {
      calories: {
        day: vi.fn().mockResolvedValue({ entries: [], total: 0 }),
      },
    },
  },
}));

it('keeps text when daily priorities relocate and recreate the calorie card', () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function Layout({ priority }: { priority: boolean }) {
    return (
      <QueryClientProvider client={client}>
        <div>{priority && <CaloriesCard />}</div>
        <section>{!priority && <CaloriesCard />}</section>
      </QueryClientProvider>
    );
  }
  const view = render(<Layout priority={false} />);
  const original = screen.getByPlaceholderText('chicken breast and rice, ~600');
  fireEvent.change(original, {
    target: { value: 'Correcting my meal description' },
  });
  view.rerender(<Layout priority />);
  const restored = screen.getByDisplayValue('Correcting my meal description');
  expect(restored).not.toBe(original);
});
