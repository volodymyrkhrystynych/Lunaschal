// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, type TrendWeek } from '@/hooks/api';
import { TrendsChart } from './TrendsChart';

vi.mock('@/hooks/api', () => ({
  api: { lifestyle: { trends: vi.fn() } },
}));

const trends = vi.mocked(api.lifestyle.trends);

const week = (
  weekStart: string,
  applications = 0,
  journalEntries = 0
): TrendWeek => ({ weekStart, applications, journalEntries });

beforeEach(() => {
  vi.clearAllMocks();
});

function renderChart() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <TrendsChart />
    </QueryClientProvider>
  );
}

describe('TrendsChart', () => {
  it('names both series in a legend and shows the latest week values', async () => {
    trends.mockResolvedValue({
      weeks: [week('2026-08-03', 2, 5), week('2026-08-10', 7, 1)],
    });
    const { container } = renderChart();

    // A legend is always present for two series — identity is never carried by
    // colour alone. Latest week's readout sits beside each name.
    expect(await screen.findByText('7')).not.toBeNull();
    expect(screen.getByText('Applications sent')).not.toBeNull();
    expect(screen.getByText('Journal entries')).not.toBeNull();
    expect(screen.getByText('1')).not.toBeNull();

    expect(container.querySelectorAll('path')).toHaveLength(2);
  });

  it('draws both lines on one shared axis', async () => {
    // Equal values must land on the same y, which is what a second scale would
    // break. 4 and 4 in the same week, one line each.
    trends.mockResolvedValue({
      weeks: [week('2026-08-03', 0, 8), week('2026-08-10', 4, 4)],
    });
    const { container } = renderChart();
    await waitFor(() =>
      expect(container.querySelectorAll('path')).toHaveLength(2)
    );

    const [a, b] = [...container.querySelectorAll('path')].map(
      p => p.getAttribute('d')!.split(' ')[1]
    );
    expect(a).toBe(b);
  });

  it('says so rather than drawing an empty box when there is no data', async () => {
    trends.mockResolvedValue({ weeks: [] });
    renderChart();

    expect(await screen.findByText('No data yet')).not.toBeNull();
  });

  it('plots a run of zero weeks as a line instead of dropping them', async () => {
    trends.mockResolvedValue({
      weeks: [week('2026-08-03'), week('2026-08-10'), week('2026-08-17')],
    });
    const { container } = renderChart();
    await waitFor(() =>
      expect(container.querySelectorAll('path')).toHaveLength(2)
    );

    // Three points per series, flat and centred — not an absent line.
    const d = container.querySelector('path')!.getAttribute('d')!;
    expect(d.split(' ')).toHaveLength(3);
  });
});

describe('TrendsChart markers', () => {
  // The viewBox is fixed at 320 units wide and the height is locked in CSS
  // pixels, so x and y scale independently — a `circle r` painted the hovered
  // week ~3.5x too wide on the desktop's full-width card.
  it('draws the hovered marker as an ellipse whose radii account for the stretch', async () => {
    trends.mockResolvedValue({
      weeks: [week('2026-08-03', 2, 5), week('2026-08-10', 7, 1)],
    });
    const { container } = renderChart();
    await waitFor(() =>
      expect(container.querySelectorAll('path')).toHaveLength(2)
    );

    const svg = container.querySelector('svg')!;
    fireEvent.pointerMove(svg, { clientX: 10 });

    const markers = [...container.querySelectorAll('ellipse')];
    expect(markers).toHaveLength(2);
    for (const m of markers) {
      // jsdom reports a zero-width box, so the hook holds at scale 1 and the
      // radii match — which is the un-stretched geometry the chart always
      // meant to draw.
      expect(m.getAttribute('rx')).toBe(m.getAttribute('ry'));
      // The surface ring must not stretch either, or it becomes the blob it
      // exists to prevent.
      expect(m.getAttribute('vector-effect')).toBe('non-scaling-stroke');
    }
    expect(container.querySelectorAll('circle')).toHaveLength(0);
  });

  it('shrinks the x-radius by however much the card stretched the chart', async () => {
    // The measured desktop case: a max-w-6xl card leaves the SVG ~1120px, so
    // one viewBox unit is 3.5 CSS px across and 1 down. A 4.5-unit radius has
    // to become ~1.29 units wide to come out round.
    const rect = vi
      .spyOn(Element.prototype, 'getBoundingClientRect')
      .mockReturnValue({
        width: 1120,
        height: 120,
        left: 0,
        top: 0,
      } as DOMRect);

    trends.mockResolvedValue({
      weeks: [week('2026-08-03', 2, 5), week('2026-08-10', 7, 1)],
    });
    const { container } = renderChart();
    await waitFor(() =>
      expect(container.querySelectorAll('path')).toHaveLength(2)
    );
    fireEvent.pointerMove(container.querySelector('svg')!, { clientX: 10 });

    const [marker] = [...container.querySelectorAll('ellipse')];
    await waitFor(() =>
      expect(Number(marker.getAttribute('rx'))).toBeCloseTo(4.5 / 3.5, 3)
    );
    expect(Number(marker.getAttribute('ry'))).toBe(4.5);
    rect.mockRestore();
  });
});
