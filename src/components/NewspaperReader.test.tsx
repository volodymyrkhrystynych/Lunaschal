// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, api } from '../hooks/api';
import { NewspaperReader } from './NewspaperReader';

vi.mock('pdfjs-dist', () => ({
  GlobalWorkerOptions: {},
  getDocument: () => ({
    promise: Promise.resolve({ numPages: 1 }),
    destroy: vi.fn(),
  }),
}));
vi.mock('../hooks/api', () => ({
  // Carries a status, like the real one: the reader has to tell a 409 (another
  // reader moved the markup on) from any other refusal, and a bare Error cannot.
  ApiError: class extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
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
  // The markup layer, not the tool panel's own icons.
  const svg = container.querySelector('[aria-label="Page 1"] svg')!;
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
  /** The tools moved into the floating panel, where they are icons with
   * accessible names rather than text. */
  const pick = (name: string) =>
    fireEvent.click(screen.getByRole('button', { name }));
  return { svg, pointer, touchMove, pick };
}

describe('newspaper pencil and finger input', () => {
  it('records Pencil coordinates and ignores finger pointers entirely', async () => {
    const { svg, pointer, pick } = await openPage();
    pick('Pen');
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
            // Pressure rides along as a third coordinate; a pointer with none
            // reports 0.5, as a mouse does. The width is the medium pen and the
            // colour the one this reader has always drawn in.
            points: [
              [0.1, 0.1, 0.5],
              [0.4, 0.3, 0.5],
            ],
            size: 3,
            color: '#1756ad',
          },
        ],
      })
    );
  });

  it('cancels a stylus touch so the Pencil writes instead of scrolling', async () => {
    const { touchMove, pick } = await openPage();
    pick('Pen');
    expect(touchMove('stylus')).toBe(true);
  });

  it('leaves finger scrolling to the browser while marking', async () => {
    const { touchMove, pick } = await openPage();
    pick('Highlighter');
    expect(touchMove('direct')).toBe(false);
    // A palm beside the nib must not scroll either, but only two fingers can
    // pinch-zoom, so a stylus riding along with a finger is not cancelled.
    expect(touchMove('direct', 'stylus')).toBe(false);
  });

  it('lets the Pencil scroll again in Read mode', async () => {
    const { touchMove } = await openPage();
    expect(
      screen.getByRole('button', { name: 'Read' }).getAttribute('aria-pressed')
    ).toBe('true');
    expect(touchMove('stylus')).toBe(false);
  });

  it('holds every touch off while a stroke is in progress', async () => {
    const { pointer, touchMove, pick } = await openPage();
    pick('Pen');
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
    const { pointer, pick } = await openPage();
    pick('Pen');
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
    // Read in the old shape, written back in the current one: an unpressured
    // point becomes full pressure, which is the width it was always drawn at.
    await waitFor(() =>
      expect(api.newspapers.saveMarkup).toHaveBeenCalledWith(issue.date, {
        revision: 0,
        strokes: [
          {
            page: 1,
            tool: 'pen',
            points: [
              [0.1, 0.2, 1],
              [0.3, 0.4, 1],
            ],
            size: 2,
          },
        ],
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

  it('undoes a stroke made in this session, and saves the result', async () => {
    const { pointer, pick } = await openPage();
    pick('Pen');
    pointer('pointerdown', 'pen', 10, 20);
    pointer('pointermove', 'pen', 40, 60);
    pointer('pointerup', 'pen', 40, 60);

    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    fireEvent.click(screen.getByText('Save now'));
    await waitFor(() =>
      expect(api.newspapers.saveMarkup).toHaveBeenCalledWith(issue.date, {
        revision: 0,
        strokes: [],
      })
    );
  });

  it('cannot rewind markup made in an earlier sitting', async () => {
    // Undo used to be `strokes.slice(0, -1)` over the whole issue, so it could
    // peel a stroke off the stored record — including one on a page that was
    // not even on screen. It is a session history now, like the Paper editor's,
    // and the eraser is the answer for ink from an earlier sitting. That the
    // eraser is page-local is the other half of the fix.
    vi.mocked(api.newspapers.markup).mockResolvedValue({
      revision: 3,
      strokes: [stroke],
    } as never);
    render(<NewspaperReader issue={issue} onClose={vi.fn()} />);
    await screen.findByText('Save now');
    expect(
      screen.getByRole('button', { name: 'Undo' }).hasAttribute('disabled')
    ).toBe(true);
  });
});

describe('a conflicting save', () => {
  // The server answers 409 when another reader has already moved the markup on
  // (newspaper_issues.py's compare-and-swap on `revision`). Until this was
  // fixed the reader could not see that: `saveMarkup` went through the plain
  // `send`, which throws an `Error` with no status, so `e instanceof ApiError`
  // was never true. The conflict flag stayed false, "Use server copy" never
  // appeared, and the 1.5 s autosave re-sent the same stale revision forever.
  it('offers the server copy and stops re-sending the stale revision', async () => {
    vi.mocked(api.newspapers.saveMarkup).mockRejectedValue(
      new ApiError('Markup changed in another reader.', 409)
    );
    const { pointer, pick } = await openPage();
    pick('Pen');
    pointer('pointerdown', 'pen', 10, 20);
    pointer('pointerup', 'pen', 10, 20);

    fireEvent.click(screen.getByText('Save now'));
    await screen.findByText('Use server copy');
    const attempts = vi.mocked(api.newspapers.saveMarkup).mock.calls.length;

    // Every later save is refused before it reaches the network, so the loop
    // cannot keep hammering a request the server has already decided on.
    fireEvent.click(screen.getByText('Save now'));
    fireEvent.click(screen.getByText('Save now'));
    await waitFor(() =>
      expect(api.newspapers.saveMarkup).toHaveBeenCalledTimes(attempts)
    );
  });
});

describe('the eraser the reader never had', () => {
  it('rubs out a stroke and saves what survived', async () => {
    const { svg, pointer, pick } = await openPage();
    pick('Pen');
    pointer('pointerdown', 'pen', 10, 20);
    pointer('pointermove', 'pen', 40, 60);
    pointer('pointerup', 'pen', 40, 60);
    expect(svg.querySelectorAll('polyline')).toHaveLength(1);

    pick('Eraser');
    // Scrubbed straight over the stroke that was just drawn.
    pointer('pointerdown', 'pen', 10, 20);
    pointer('pointermove', 'pen', 40, 60);
    pointer('pointerup', 'pen', 40, 60);
    expect(svg.querySelectorAll('polyline')).toHaveLength(0);

    fireEvent.click(screen.getByText('Save now'));
    await waitFor(() =>
      expect(api.newspapers.saveMarkup).toHaveBeenCalledWith(issue.date, {
        revision: 0,
        strokes: [],
      })
    );
  });

  it('does not spend an undo step on a scrub that touched nothing', async () => {
    const { pointer, pick } = await openPage();
    pick('Eraser');
    pointer('pointerdown', 'pen', 10, 20);
    pointer('pointermove', 'pen', 40, 60);
    pointer('pointerup', 'pen', 40, 60);
    expect(
      screen.getByRole('button', { name: 'Undo' }).hasAttribute('disabled')
    ).toBe(true);
  });

  it('never lays down ink of its own', async () => {
    // The eraser stroke is consumed, not stored: it removes what it crossed and
    // leaves nothing behind, so it can work over a transparent page.
    const { svg, pointer, pick } = await openPage();
    pick('Eraser');
    pointer('pointerdown', 'pen', 10, 20);
    pointer('pointermove', 'pen', 40, 60);
    expect(svg.querySelectorAll('polyline')).toHaveLength(0);
    pointer('pointerup', 'pen', 40, 60);
    expect(svg.querySelectorAll('polyline')).toHaveLength(0);
  });
});
