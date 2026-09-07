// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../hooks/api';
import { NewspaperReader } from './NewspaperReader';

vi.mock('pdfjs-dist', () => ({
  GlobalWorkerOptions: {},
  getDocument: () => ({
    promise: Promise.resolve({ numPages: 1 }),
    destroy: vi.fn(),
  }),
}));
vi.mock('../hooks/api', () => ({
  ApiError: class extends Error {},
  api: { newspapers: { markup: vi.fn(), saveMarkup: vi.fn() } },
}));

const issue = {
  date: '2026-09-01',
  byteSize: 1000,
  pageCount: 1,
  pdfUrl: '/issue.pdf',
};
const draftKey = 'newspaper-markup:2026-09-01';
const stroke = {
  page: 1,
  tool: 'pen',
  points: [
    [0.1, 0.2],
    [0.3, 0.4],
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  vi.stubGlobal(
    'IntersectionObserver',
    class {
      observe() {}
      disconnect() {}
    }
  );
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    }
  );
  vi.mocked(api.newspapers.markup).mockResolvedValue({
    revision: 0,
    strokes: [],
  });
  vi.mocked(api.newspapers.saveMarkup).mockResolvedValue({ revision: 1 });
});

describe('newspaper markup recovery', () => {
  it('records Pencil coordinates while finger input scrolls without drawing', async () => {
    const { container } = render(
      <NewspaperReader issue={issue} onClose={vi.fn()} />
    );
    await screen.findByText('Save now');
    fireEvent.click(screen.getByText('Pen'));
    const svg = container.querySelector('svg')!;
    const captured = new Set<number>();
    svg.setPointerCapture = id => {
      captured.add(id);
    };
    svg.hasPointerCapture = id => captured.has(id);
    svg.releasePointerCapture = id => {
      captured.delete(id);
    };
    svg.getBoundingClientRect = () =>
      ({ left: 0, top: 0, width: 100, height: 200 }) as DOMRect;
    const scroller = svg.parentElement!.parentElement!;
    scroller.scrollBy = vi.fn();
    function pointer(name: string, type: string, x: number, y: number) {
      const event = new Event(name, { bubbles: true });
      Object.assign(event, {
        pointerType: type,
        pointerId: type === 'touch' ? 1 : 2,
        isPrimary: true,
        clientX: x,
        clientY: y,
      });
      fireEvent(svg, event);
    }
    pointer('pointerdown', 'touch', 10, 20);
    pointer('pointermove', 'touch', 10, 40);
    pointer('pointerup', 'touch', 10, 40);
    expect(scroller.scrollBy).toHaveBeenCalledWith(0, -20);
    expect(svg.querySelectorAll('polyline')).toHaveLength(0);
    pointer('pointerdown', 'pen', 10, 20);
    pointer('pointermove', 'pen', 40, 60);
    pointer('pointerup', 'pen', 40, 60);
    fireEvent.click(screen.getByText('Save now'));
    await waitFor(() =>
      expect(api.newspapers.saveMarkup).toHaveBeenCalledWith(issue.date, {
        revision: 0,
        strokes: [
          {
            page: 1,
            tool: 'pen',
            points: [
              [0.1, 0.1],
              [0.4, 0.3],
            ],
          },
        ],
      })
    );
  });

  it('recovers a local draft and clears it only after server acknowledgement', async () => {
    localStorage.setItem(
      draftKey,
      JSON.stringify({ revision: 0, strokes: [stroke] })
    );
    render(<NewspaperReader issue={issue} onClose={vi.fn()} />);
    fireEvent.click(await screen.findByText('Save now'));
    await waitFor(() =>
      expect(api.newspapers.saveMarkup).toHaveBeenCalledWith(issue.date, {
        revision: 0,
        strokes: [stroke],
      })
    );
    await waitFor(() => expect(localStorage.getItem(draftKey)).toBeNull());
    expect(screen.getByRole('status').textContent).toBe('Saved');
  });

  it('retains a draft after a failed save and allows closing with the local copy', async () => {
    localStorage.setItem(
      draftKey,
      JSON.stringify({ revision: 0, strokes: [stroke] })
    );
    vi.mocked(api.newspapers.saveMarkup).mockRejectedValue(
      new Error('Offline')
    );
    const close = vi.fn();
    render(<NewspaperReader issue={issue} onClose={close} />);
    fireEvent.click(await screen.findByText('Save now'));
    await screen.findByText('Not saved to server: Offline');
    expect(localStorage.getItem(draftKey)).not.toBeNull();
    fireEvent.click(screen.getByText('Close with local draft'));
    expect(close).toHaveBeenCalled();
  });

  it('does not overwrite newer server markup with a stale recovered draft', async () => {
    localStorage.setItem(
      draftKey,
      JSON.stringify({ revision: 0, strokes: [stroke] })
    );
    vi.mocked(api.newspapers.markup).mockResolvedValue({
      revision: 2,
      strokes: [],
    });
    render(<NewspaperReader issue={issue} onClose={vi.fn()} />);
    await screen.findByText(/local draft conflicts/);
    fireEvent.click(screen.getByText('Save now'));
    expect(api.newspapers.saveMarkup).not.toHaveBeenCalled();
    expect(localStorage.getItem(draftKey)).not.toBeNull();
  });

  it('saves undo as a new revision', async () => {
    vi.mocked(api.newspapers.markup).mockResolvedValue({
      revision: 3,
      strokes: [stroke],
    } as never);
    render(<NewspaperReader issue={issue} onClose={vi.fn()} />);
    await screen.findByText('Save now');
    fireEvent.click(screen.getByText('Undo'));
    fireEvent.click(screen.getByText('Save now'));
    await waitFor(() =>
      expect(api.newspapers.saveMarkup).toHaveBeenCalledWith(issue.date, {
        revision: 3,
        strokes: [],
      })
    );
  });
});
