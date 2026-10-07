// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, type PomodoroDay } from '@/hooks/api';
import { FocusCard } from './FocusCard';

vi.mock('@/hooks/api', () => ({
  api: { lifestyle: { pomodoro: { summary: vi.fn() } } },
}));

const summary = vi.mocked(api.lifestyle.pomodoro.summary);

function renderCard() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <FocusCard />
    </QueryClientProvider>
  );
}

const day = (
  date: string,
  focusMinutes: number,
  blocks: number
): PomodoroDay => ({
  date,
  focusMinutes,
  breakMinutes: 0,
  timeoutMinutes: 0,
  completedBlocks: blocks,
});

describe('FocusCard', () => {
  it('points at the Watch before any run has been logged', async () => {
    summary.mockResolvedValue({
      days: [day('2026-10-06', 0, 0)],
      sessions: [],
    });
    renderCard();
    expect(await screen.findByText(/No timer runs yet/)).toBeTruthy();
  });

  it("shows today's focus, the window's totals and the latest runs", async () => {
    summary.mockResolvedValue({
      days: [day('2026-10-05', 50, 2), day('2026-10-06', 37, 1)],
      sessions: [
        {
          id: 'a',
          kind: 'work',
          date: '2026-10-06',
          startedAt: '2026-10-06T14:00:00+00:00',
          endedAt: '2026-10-06T14:12:00+00:00',
          plannedSeconds: 1500,
          completed: false,
          createdAt: '2026-10-06T14:12:00+00:00',
        },
      ],
    });
    renderCard();
    expect(await screen.findByText(/Today: 37 min · 1 blocks/)).toBeTruthy();
    expect(screen.getByText('87 min · 3 blocks')).toBeTruthy();
    expect(screen.getByText('Focus · 12 of 25 min (cancelled)')).toBeTruthy();
  });
});
