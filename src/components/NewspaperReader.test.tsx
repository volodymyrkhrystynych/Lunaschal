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

/** The reader's page surface, wired up the way a browser would wire it. */
async function openPage() {
  const { container } = render(
    <NewspaperReader issue={issue} onClose={vi.fn()} />
  );
  await screen.findByText('Save now');
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
  // The page div is what carries the non-passive touchmove listener; jsdom has
  // no Touch constructor, so the touches are plain objects.
  function touchMove(...types: ('direct' | 'stylus')[]) {
    const event = new Event('touchmove', { bubbles: true, cancelable: true });
    Object.assign(event, {
      touches: types.map(touchType => ({ touchType, clientX: 0, clientY: 0 })),
    });
    svg.parentElement!.dispatchEvent(event);
    return event.defaultPrevented;
  }
  return { svg, pointer, touchMove };
}

describe('newspaper pencil and finger input', () => {
  it('records Pencil coordinates and ignores finger pointers entirely', async () => {
    const { svg, pointer } = await openPage();
    fireEvent.click(screen.getByText('Pen'));
    pointer('pointerdown', 'touch', 10, 20);
    pointer('pointermove', 'touch', 10, 40);
    pointer('pointerup', 'touch', 10, 40);
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

  it('cancels a stylus touch so the Pencil writes instead of scrolling', async () => {
    const { touchMove } = await openPage();
    fireEvent.click(screen.getByText('Pen'));
    expect(touchMove('stylus')).toBe(true);
  });

  it('leaves finger scrolling to the browser while marking', async () => {
    const { touchMove } = await openPage();
    fireEvent.click(screen.getByText('Highlight'));
    expect(touchMove('direct')).toBe(false);
    // A palm beside the nib must not scroll either, but only two fingers can
    // pinch-zoom, so a stylus riding along with a finger is not cancelled.
    expect(touchMove('direct', 'stylus')).toBe(false);
  });

  it('lets the Pencil scroll again in Read mode', async () => {
    const { touchMove } = await openPage();
    expect(screen.getByText('Read').getAttribute('aria-pressed')).toBe('true');
    expect(touchMove('stylus')).toBe(false);
  });

  it('holds every touch off while a stroke is in progress', async () => {
    const { pointer, touchMove } = await openPage();
    fireEvent.click(screen.getByText('Pen'));
    pointer('pointerdown', 'pen', 10, 20);
    // A resting palm would otherwise drag the page out from under the nib.
    expect(touchMove('direct')).toBe(true);
    pointer('pointerup', 'pen', 10, 20);
    expect(touchMove('direct')).toBe(false);
  });

  it('leaves the toolbar unchanged across an ordinary save', async () => {
    // The bar wraps, so anything that grows or appears in it costs a line and
    // gives it back. A stroke used to write a long status *and* reveal a close
    // button, so the bar changed height twice per stroke — which is unusable
    // to write against.
    const { pointer } = await openPage();
    fireEvent.click(screen.getByText('Pen'));
    pointer('pointerdown', 'pen', 10, 20);
    pointer('pointerup', 'pen', 10, 20);
    expect(screen.queryByText('Close with local draft')).toBeNull();
    expect(screen.getByRole('status').textContent).not.toMatch(/iPad/);
    fireEvent.click(screen.getByText('Save now'));
    expect(screen.queryByText('Close with local draft')).toBeNull();
    await waitFor(() =>
      expect(screen.getByRole('status').textContent).toBe('Saved')
    );
    expect(screen.queryByText('Close with local draft')).toBeNull();
  });

  it('never lets a finger scroll be blocked in Read mode', async () => {
    const { touchMove } = await openPage();
    expect(touchMove('direct')).toBe(false);
  });
});

describe('newspaper markup recovery', () => {
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
