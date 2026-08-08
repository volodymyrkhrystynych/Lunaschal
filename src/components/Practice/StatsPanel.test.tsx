// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { PracticeStats } from '../../hooks/api';
import { StatsPanel } from './StatsPanel';

// Seeds the query cache directly, the way the IndexedDB persister does on
// restore — no fetch involved, which is exactly the situation that broke: the
// panel renders against restored data before any refetch can correct its shape.
function renderWithCache(stats: unknown) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  qc.setQueryData(['practice', 'stats'], stats);
  return render(
    <QueryClientProvider client={qc}>
      <StatsPanel />
    </QueryClientProvider>
  );
}

const current: PracticeStats = {
  totalAttempts: 12,
  avgAccuracy: 93,
  avgWpm: 29,
  byLanguage: { react: { attempts: 12, avgAccuracy: 93, avgWpm: 29 } },
  recall: { attempts: 4, passes: 3, passRate: 75 },
};

describe('StatsPanel', () => {
  it('reports recall on its own line, apart from the typing averages', () => {
    renderWithCache(current);

    expect(screen.getByText(/12 attempts/)).toBeTruthy();
    expect(screen.getByText(/4 from memory/)).toBeTruthy();
    expect(screen.getByText(/75% recalled/)).toBeTruthy();
  });

  it('renders a pre-recall cached shape instead of crashing the tab', () => {
    // What a persisted row written before the blind-recall feature looks like:
    // no `recall` key at all. Reading through it used to throw during render,
    // and with no error boundary that blanked the entire Practice view.
    const { recall: _recall, ...preRecall } = current;

    renderWithCache(preRecall);

    expect(screen.getByText(/12 attempts/)).toBeTruthy();
    expect(screen.queryByText(/from memory/)).toBeNull();
  });

  it('stays hidden with nothing practised either way', () => {
    const { container } = renderWithCache({
      totalAttempts: 0,
      avgAccuracy: null,
      avgWpm: null,
      byLanguage: {},
      recall: { attempts: 0, passes: 0, passRate: null },
    } satisfies PracticeStats);

    expect(container.firstChild).toBeNull();
  });
});
